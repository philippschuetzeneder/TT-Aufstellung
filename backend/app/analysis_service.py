from __future__ import annotations

from collections import Counter, defaultdict
from itertools import permutations
import math
import time
from sqlalchemy import text, bindparam
from .db import SessionLocal

WIN_TARGET = 8
SINGLE_GAMES = 12
MAX_ANALYSIS_SECONDS = 5.0


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


def _team_win_probability(probs):
    distribution = [1.0]
    for p in probs:
        nxt = [0.0] * (len(distribution) + 1)
        for wins, mass in enumerate(distribution):
            nxt[wins] += mass * (1.0 - p)
            nxt[wins + 1] += mass * p
        distribution = nxt
    return sum(distribution[WIN_TARGET:])


def _position_index(position):
    value = str(position or '').strip().upper()
    return {'A': 0, 'B': 1, 'C': 2, 'D': 3, '1': 0, '2': 1, '3': 2, '4': 3}.get(value)


def _raw_team_lineup_scenarios(db, team, required_ids=None):
    """Build position-order probabilities from only this team's raw matches.

    This is deliberately small.  It is also the important fallback when the
    precomputed cache has no lineup rows.  We never replace real historical
    position frequencies with 24 equally weighted permutations unless there
    is genuinely no historical four-player position order available.
    """
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

    counts = Counter()
    names = {}
    required = set(str(x) for x in required_ids) if required_ids is not None else None

    for players in matches.values():
        by_id = {}
        for r in players:
            pid = str(r['player_id'])
            by_id.setdefault(pid, r)
            names.setdefault(pid, r['player_name'])
        ids = set(by_id)
        if required is not None:
            if ids != required:
                continue
        elif len(ids) != 4:
            continue

        order = [None] * 4
        valid = True
        for pid in ids:
            idx = _position_index(by_id[pid]['position'])
            if idx is None or order[idx] is not None:
                valid = False
                break
            order[idx] = pid
        if valid and all(order):
            counts[tuple(order)] += 1

    total = sum(counts.values())
    if not total:
        return [], names

    ordered = counts.most_common(24)
    scenarios = [(count / total, order) for order, count in ordered]
    return scenarios, names


def _raw_four_player_lineup(db, team):
    scenarios, names = _raw_team_lineup_scenarios(db, team)
    if scenarios:
        return scenarios, names
    return [], names


def _load_analysis_data(own, opponent_team, actual):
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '5000ms'"))
        db.execute(text("SET lock_timeout = '500ms'"))
        own = [str(x) for x in own]
        actual = None if actual is None else [str(x) for x in actual]
        fallback_names = {}

        if actual is not None:
            lineup_key = ','.join(sorted(actual))
            rows = list(db.execute(text("""
                SELECT p1,p2,p3,p4,appearances
                FROM analysis_lineup_orders
                WHERE lineup_key=:key
                ORDER BY appearances DESC
                LIMIT 24
            """), {'key': lineup_key}).mappings())
            total = sum(int(r['appearances'] or 0) for r in rows)
            if total:
                scenarios = [(int(r['appearances']) / total, tuple(str(r[k]) for k in ('p1','p2','p3','p4'))) for r in rows]
            else:
                scenarios, fallback_names = _raw_team_lineup_scenarios(db, opponent_team, actual)
                if not scenarios:
                    scenarios = [(1.0 / 24.0, tuple(order)) for order in permutations(actual)]
            source = 'actual-historical-cache-or-raw'
        else:
            rows = list(db.execute(text("""
                SELECT p1,p2,p3,p4,appearances
                FROM analysis_lineup_orders
                WHERE team=:team
                ORDER BY appearances DESC
                LIMIT 24
            """), {'team': opponent_team}).mappings())
            total = sum(int(r['appearances'] or 0) for r in rows)
            if total:
                scenarios = [(int(r['appearances']) / total, tuple(str(r[k]) for k in ('p1','p2','p3','p4'))) for r in rows]
                source = 'predicted-historical-cache'
            else:
                scenarios, fallback_names = _raw_four_player_lineup(db, opponent_team)
                source = 'predicted-historical-raw'
                if not scenarios:
                    return {}, {}, {}, [], source

        if not scenarios:
            return {}, {}, {}, [], source

        relevant = set(own)
        for _, order in scenarios:
            relevant.update(order)
        ids = list(relevant)

        stats = {}
        names = dict(fallback_names)

        stats_stmt = text("""
            SELECT player_id::text AS player_id, player_name, singles_wins, singles_games
            FROM analysis_player_stats
            WHERE player_id::text IN :ids
        """).bindparams(bindparam('ids', expanding=True))
        for r in db.execute(stats_stmt, {'ids': ids}).mappings():
            pid = str(r['player_id'])
            names[pid] = r['player_name']
            stats[pid] = (int(r['singles_wins'] or 0), int(r['singles_games'] or 0))

        # Always resolve display names from the raw match table as a final,
        # cheap lookup.  IDs are for computation; users should never see IDs.
        name_stmt = text("""
            SELECT external_player_id::text AS player_id, max(name) AS player_name
            FROM match_players
            WHERE external_player_id::text IN :ids
            GROUP BY external_player_id
        """).bindparams(bindparam('ids', expanding=True))
        for r in db.execute(name_stmt, {'ids': ids}).mappings():
            names[str(r['player_id'])] = r['player_name']

        matchup_stmt = text("""
            SELECT player_id::text AS player_id, opponent_id::text AS opponent_id, wins, games
            FROM analysis_matchups
            WHERE player_id::text IN :ids
              AND opponent_id::text IN :ids
        """).bindparams(bindparam('ids', expanding=True))
        matchups = {}
        for r in db.execute(matchup_stmt, {'ids': ids}).mappings():
            matchups[(str(r['player_id']), str(r['opponent_id']))] = (int(r['wins'] or 0), int(r['games'] or 0))

        for pid in ids:
            stats.setdefault(pid, (0, 0))
            names.setdefault(pid, pid)
        return names, stats, matchups, scenarios, source
    except Exception as exc:
        raise RuntimeError(f'Analyse-Daten konnten nicht geladen werden: {type(exc).__name__}: {exc}') from exc
    finally:
        db.close()


def analyze_lineup(own_player_ids, opponent_team, actual_opponent_ids=None, opponent_limit=24):
    started = time.monotonic()
    own = [str(x) for x in own_player_ids]
    if len(own) != 4 or len(set(own)) != 4:
        raise ValueError('exactly four different own_player_ids are required')
    if not opponent_team:
        raise ValueError('opponent_team is required')
    actual = None if actual_opponent_ids is None else [str(x) for x in actual_opponent_ids]
    if actual is not None and (len(actual) != 4 or len(set(actual)) != 4):
        raise ValueError('exactly four different actual_opponent_ids are required')

    names, stats, matchups, scenarios, source = _load_analysis_data(own, opponent_team, actual)
    if not scenarios:
        return {'ok': True, 'phase': 'B' if actual is not None else 'A', 'recommendations': [], 'warnings': [f'Keine historische 4-Spieler-Aufstellung für {opponent_team} gefunden.']}

    schedule = ((0,0),(1,1),(2,2),(3,3),(0,1),(1,0),(2,3),(3,2),(0,2),(2,0),(1,3),(3,1))
    relevant = set(own)
    for _, order in scenarios:
        relevant.update(order)
    matchup_p = {(a,b): _matchup_probability(a,b,stats,matchups) for a in relevant for b in relevant if a != b}

    evaluated = []
    for own_order in permutations(own):
        expected = 0.0
        for scenario_probability, opp_order in scenarios:
            singles = [matchup_p.get((own_order[h], opp_order[a]), 0.5) for h,a in schedule]
            doubles = [
                _pair_probability(own_order[0], own_order[1], opp_order[0], opp_order[1], stats),
                _pair_probability(own_order[2], own_order[3], opp_order[2], opp_order[3], stats),
            ]
            expected += scenario_probability * _team_win_probability(singles + doubles)
        evaluated.append({'own_player_ids': list(own_order), 'players': [names.get(pid, pid) for pid in own_order], 'team_win_probability': round(expected, 6)})
        if time.monotonic() - started > MAX_ANALYSIS_SECONDS:
            raise RuntimeError('Analysis exceeded the internal 5-second safety budget')

    evaluated.sort(key=lambda x: (-x['team_win_probability'], x['own_player_ids']))
    for rank, item in enumerate(evaluated, 1):
        item['rank'] = rank

    opponent_predictions = [
        {'player_ids': list(order), 'players': [{'id': p, 'name': names.get(p, p)} for p in order], 'probability': round(probability, 6)}
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
        'model': {'version': 'strength-h2h-doubles-v19', 'win_target': WIN_TARGET, 'single_games': SINGLE_GAMES, 'doubles_games': 2, 'opponent_lineups': 'historical four-player position orders; scoped raw fallback', 'actual_opponent_positions': 'historical distribution; all 24 permutations only when no historical order exists'},
        'data_quality': {'scenario_variants': len(scenarios), 'own_orders_evaluated': 24, 'runtime_seconds': round(elapsed,4), 'runtime_data_source': 'analysis-cache-with-scoped-raw-fallback', 'missing_player_stats_use_neutral_prior': True, 'position_probabilities_observed': source != 'all-24-uniform-fallback'},
    }
