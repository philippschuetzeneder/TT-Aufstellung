from __future__ import annotations

from collections import Counter, defaultdict
from itertools import permutations
import math
import time
from sqlalchemy import text, bindparam
from .db import SessionLocal

# A team match always consists of exactly 10 games:
#   8 singles: every player plays exactly twice
#   2 doubles
# Therefore 8:0 and 8:1 are impossible; 10:0 and 9:1 are possible.
SINGLE_GAMES = 8
TOTAL_GAMES = 10
WIN_TARGET = 6  # team wins iff it wins at least 6 of the 10 games; 5:5 is a draw
MAX_ANALYSIS_SECONDS = 5.0

# Fixed singles order.  Every player appears exactly twice:
# 1) 1-v-1, 2) 2-v-2, 3) 3-v-3, 4) 4-v-4,
# 5) 1-v-2, 6) 2-v-1, 7) 3-v-4, 8) 4-v-3.
SINGLES_SCHEDULE = ((0, 0), (1, 1), (2, 2), (3, 3),
                    (0, 1), (1, 0), (2, 3), (3, 2))


def _strength(wins, games):
    return (wins + 5.0) / (games + 10.0)


def _matchup_probability(a, b, stats, matchups):
    aw, ag = stats.get(a, (0, 0)); bw, bg = stats.get(b, (0, 0))
    base = 1.0 / (1.0 + math.exp(-7.0 * (_strength(aw, ag) - _strength(bw, bg))))
    wins, games = matchups.get((a, b), (0, 0))
    if not games:
        return base
    direct = (wins + 2.0) / (games + 4.0)
    weight = min(0.55, games / 12.0)
    return (1.0 - weight) * base + weight * direct


def _pair_probability(a, b, c, d, stats):
    left = (_strength(*stats.get(a, (0, 0))) + _strength(*stats.get(b, (0, 0)))) / 2.0
    right = (_strength(*stats.get(c, (0, 0))) + _strength(*stats.get(d, (0, 0)))) / 2.0
    return 1.0 / (1.0 + math.exp(-7.0 * (left - right)))


def _doubles_pairs(order, stats):
    """Return the assumed doubles pairing: two strongest together, two weakest together."""
    ranked = sorted(order, key=lambda pid: _strength(*stats.get(pid, (0, 0))), reverse=True)
    return (ranked[0], ranked[1]), (ranked[2], ranked[3])


def _team_result_probabilities(probs):
    """Return P(win), P(draw), P(loss) for exactly ten independent game probabilities."""
    if len(probs) != TOTAL_GAMES:
        raise ValueError(f'expected {TOTAL_GAMES} game probabilities, got {len(probs)}')
    distribution = [1.0]
    for p in probs:
        nxt = [0.0] * (len(distribution) + 1)
        for wins, mass in enumerate(distribution):
            nxt[wins] += mass * (1.0 - p)
            nxt[wins + 1] += mass * p
        distribution = nxt
    draw = distribution[5]
    win = sum(distribution[WIN_TARGET:])
    loss = sum(distribution[:5])
    return win, draw, loss


def _position_index(position):
    value = str(position or '').strip().upper()
    return {'A': 0, 'B': 1, 'C': 2, 'D': 3, '1': 0, '2': 1, '3': 2, '4': 3}.get(value)


def _raw_team_lineup_scenarios(db, team, required_ids=None):
    rows = db.execute(text("""
        SELECT m.id AS match_id, m.match_date, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))
          AND mp.external_player_id IS NOT NULL
        ORDER BY m.match_date DESC NULLS LAST, m.id DESC
    """), {'team': team}).mappings()
    matches = defaultdict(list)
    for row in rows:
        matches[row['match_id']].append(row)
    counts = Counter(); names = {}
    required = set(str(x) for x in required_ids) if required_ids is not None else None
    for players in matches.values():
        by_id = {}
        for r in players:
            pid = str(r['player_id']); by_id.setdefault(pid, r); names.setdefault(pid, r['player_name'])
        ids = set(by_id)
        if required is not None:
            if ids != required: continue
        elif len(ids) != 4:
            continue
        order = [None] * 4; valid = True
        for pid in ids:
            idx = _position_index(by_id[pid]['position'])
            if idx is None or order[idx] is not None:
                valid = False; break
            order[idx] = pid
        if valid and all(order): counts[tuple(order)] += 1
    total = sum(counts.values())
    if not total: return [], names
    return [(count / total, order) for order, count in counts.most_common(24)], names


def _load_raw_player_data(db, ids):
    """Load only the requested players' names/stats from raw XTTV rows."""
    ids = [str(x) for x in ids]
    if not ids:
        return {}, {}, {}
    params = {'ids': ids}
    stats_stmt = text("""
        WITH games AS (
            SELECT hp.external_player_id::text AS player_id,
                   hp.name AS player_name,
                   CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS win
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids
            UNION ALL
            SELECT ap.external_player_id::text, ap.name,
                   CASE WHEN split_part(trim(g.result),':',2)::int > split_part(trim(g.result),':',1)::int THEN 1 ELSE 0 END
            FROM match_games g
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND ap.external_player_id::text IN :ids
        )
        SELECT player_id, max(player_name) AS player_name, sum(win) AS wins, count(*) AS games
        FROM games GROUP BY player_id
    """).bindparams(bindparam('ids', expanding=True))
    names = {}; stats = {}
    for r in db.execute(stats_stmt, params).mappings():
        pid = str(r['player_id']); names[pid] = r['player_name']; stats[pid] = (int(r['wins'] or 0), int(r['games'] or 0))

    name_stmt = text("""
        SELECT external_player_id::text AS player_id, max(name) AS player_name
        FROM match_players WHERE external_player_id::text IN :ids
        GROUP BY external_player_id
    """).bindparams(bindparam('ids', expanding=True))
    for r in db.execute(name_stmt, params).mappings():
        pid = str(r['player_id'])
        if r['player_name']: names[pid] = r['player_name']

    h2h_stmt = text("""
        WITH base AS (
            SELECT hp.external_player_id::text AS home_id, ap.external_player_id::text AS away_id,
                   CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS home_win
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids AND ap.external_player_id::text IN :ids
        )
        SELECT home_id AS player_id, away_id AS opponent_id, sum(home_win) AS wins, count(*) AS games
        FROM base GROUP BY home_id, away_id
        UNION ALL
        SELECT away_id, home_id, sum(1-home_win), count(*)
        FROM base GROUP BY away_id, home_id
    """).bindparams(bindparam('ids', expanding=True))
    matchups = {}
    for r in db.execute(h2h_stmt, params).mappings():
        matchups[(str(r['player_id']), str(r['opponent_id']))] = (int(r['wins'] or 0), int(r['games'] or 0))
    return names, stats, matchups


def _load_analysis_data(own, opponent_team, actual):
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '5000ms'")); db.execute(text("SET lock_timeout = '500ms'"))
        own = [str(x) for x in own]; actual = None if actual is None else [str(x) for x in actual]
        fallback_names = {}
        if actual is not None:
            lineup_key = ','.join(sorted(actual))
            rows = list(db.execute(text("SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders WHERE lineup_key=:key ORDER BY appearances DESC LIMIT 24"), {'key': lineup_key}).mappings())
            total = sum(int(r['appearances'] or 0) for r in rows)
            if total:
                scenarios = [(int(r['appearances']) / total, tuple(str(r[k]) for k in ('p1','p2','p3','p4'))) for r in rows]; source = 'actual-historical-cache'
            else:
                scenarios, fallback_names = _raw_team_lineup_scenarios(db, opponent_team, actual); source = 'actual-historical-raw'
                if not scenarios: scenarios = [(1.0 / 24.0, tuple(order)) for order in permutations(actual)]; source = 'all-24-uniform-fallback'
        else:
            rows = list(db.execute(text("SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders WHERE team=:team ORDER BY appearances DESC LIMIT 24"), {'team': opponent_team}).mappings())
            total = sum(int(r['appearances'] or 0) for r in rows)
            if total:
                scenarios = [(int(r['appearances']) / total, tuple(str(r[k]) for k in ('p1','p2','p3','p4'))) for r in rows]; source = 'predicted-historical-cache'
            else:
                scenarios, fallback_names = _raw_team_lineup_scenarios(db, opponent_team); source = 'predicted-historical-raw'
                if not scenarios: return {}, {}, {}, [], source
        relevant = set(own)
        for _, order in scenarios: relevant.update(order)
        ids = list(relevant)
        names, stats, matchups = _load_raw_player_data(db, ids)
        names.update({k: v for k, v in fallback_names.items() if v})
        for pid in ids:
            stats.setdefault(pid, (0, 0)); names.setdefault(pid, f'Spieler {pid}')
        return names, stats, matchups, scenarios, source
    except Exception as exc:
        raise RuntimeError(f'Analyse-Daten konnten nicht geladen werden: {type(exc).__name__}: {exc}') from exc
    finally:
        db.close()


def analyze_lineup(own_player_ids, opponent_team, actual_opponent_ids=None, opponent_limit=24):
    started = time.monotonic(); own = [str(x) for x in own_player_ids]
    if len(own) != 4 or len(set(own)) != 4: raise ValueError('exactly four different own_player_ids are required')
    if not opponent_team: raise ValueError('opponent_team is required')
    actual = None if actual_opponent_ids is None else [str(x) for x in actual_opponent_ids]
    if actual is not None and (len(actual) != 4 or len(set(actual)) != 4): raise ValueError('exactly four different actual_opponent_ids are required')
    names, stats, matchups, scenarios, source = _load_analysis_data(own, opponent_team, actual)
    if not scenarios:
        return {'ok': True, 'phase': 'B' if actual is not None else 'A', 'recommendations': [], 'warnings': [f'Keine historische 4-Spieler-Aufstellung für {opponent_team} gefunden.']}

    relevant = set(own)
    for _, order in scenarios: relevant.update(order)
    matchup_p = {(a, b): _matchup_probability(a, b, stats, matchups) for a in relevant for b in relevant if a != b}
    evaluated = []
    for own_order in permutations(own):
        expected_win = expected_draw = expected_loss = 0.0
        own_doubles = _doubles_pairs(own_order, stats)
        for scenario_probability, opp_order in scenarios:
            singles = [matchup_p.get((own_order[h], opp_order[a]), 0.5) for h, a in SINGLES_SCHEDULE]
            opp_doubles = _doubles_pairs(opp_order, stats)
            doubles = [
                _pair_probability(*own_doubles[0], *opp_doubles[0], stats),
                _pair_probability(*own_doubles[1], *opp_doubles[1], stats),
            ]
            win, draw, loss = _team_result_probabilities(singles + doubles)
            expected_win += scenario_probability * win
            expected_draw += scenario_probability * draw
            expected_loss += scenario_probability * loss
        evaluated.append({
            'own_player_ids': list(own_order),
            'players': [names.get(pid, f'Spieler {pid}') for pid in own_order],
            'team_win_probability': round(expected_win, 6),
            'team_draw_probability': round(expected_draw, 6),
            'team_loss_probability': round(expected_loss, 6),
        })
        if time.monotonic() - started > MAX_ANALYSIS_SECONDS:
            raise RuntimeError('Analysis exceeded the internal 5-second safety budget')
    evaluated.sort(key=lambda x: (-x['team_win_probability'], x['own_player_ids']))
    for rank, item in enumerate(evaluated, 1): item['rank'] = rank
    opponent_predictions = [
        {'player_ids': list(order), 'players': [{'id': p, 'name': names.get(p, f'Spieler {p}')} for p in order], 'probability': round(probability, 6)}
        for probability, order in scenarios
    ]
    elapsed = time.monotonic() - started
    return {
        'ok': True,
        'phase': 'B' if actual else 'A',
        'opponent_team': opponent_team,
        'own_player_ids': own,
        'opponent_set_source': source,
        'recommendation': evaluated[0],
        'recommendations': evaluated,
        'opponent_predictions': opponent_predictions,
        'most_likely_opponent': opponent_predictions[0] if opponent_predictions else None,
        'model': {
            'version': 'strength-h2h-doubles-v21-10-games',
            'win_target': WIN_TARGET,
            'single_games': SINGLE_GAMES,
            'doubles_games': 2,
            'total_games': TOTAL_GAMES,
            'draw_possible': True,
            'singles_schedule': '1-1, 2-2, 3-3, 4-4, 1-2, 2-1, 3-4, 4-3',
            'doubles_pairing': 'two strongest together; two weakest together',
            'opponent_lineups': 'historical four-player position orders; scoped raw fallback',
            'player_stats': 'targeted raw XTTV stats for relevant IDs',
        },
        'data_quality': {
            'scenario_variants': len(scenarios),
            'own_orders_evaluated': 24,
            'runtime_seconds': round(elapsed, 4),
            'runtime_data_source': 'targeted-raw-stats-plus-lineup-cache',
            'missing_player_stats_use_neutral_prior': False,
            'position_probabilities_observed': source != 'all-24-uniform-fallback',
        },
    }
