from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from itertools import combinations, permutations
import math
import time
from sqlalchemy import text, bindparam
from .db import SessionLocal

# Match format (OÖTTV 4-player sheet, numbers in parentheses on the grid):
# - Games 1-10 are ALWAYS played.
# - Singles 1-4: D-2, A-3, C-4, B-1  (home A-D vs guest 1-4).
# - Game 5: doubles (two strongest together).
# - Singles 6-9: A-2, D-3, C-1, B-4.
# - Game 10: doubles (two weakest together).
# - If the match is not decided after game 10, singles 11-14 are
#   A-1, B-2, C-3, D-4 (third round per player).
#
# Stopping/result rule:
# - Normally the match ends as soon as one team reaches 8 wins.
# - 8:0 and 8:1 are NOT final results: play continues so that the
#   exceptional 9:1 or 10:0 result can occur.
# - Once a non-shutout score reaches 8 wins, the match is decided.
# - If all 14 games are needed and the score is 7:7, it is a draw.
SINGLE_GAMES = 12
DOUBLE_GAMES = 2
TOTAL_GAMES = 14
WIN_TARGET = 8
MAX_ANALYSIS_SECONDS = 5.0
STATS_YEARS = 3
OPPONENT_POOL_YEARS = 2
RC_BASELINE = 1400.0
RC_SCALE = 400.0
H2H_MAX_WEIGHT = 0.85
MODEL_VERSION = 'rc-h2h-homeaway-v24'

# Home index 0=A..3=D; away index 0=1..3=4 on the guest row.
SINGLES_SCHEDULE = (
    (3, 1), (0, 2), (2, 3), (1, 0),
    (0, 1), (3, 2), (2, 0), (1, 3),
    (0, 0), (1, 1), (2, 2), (3, 3),
)


def _parse_match_date(value):
    if not value:
        return None
    for fmt in ('%d.%m.%Y %H:%M', '%d.%m.%Y', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value)[:16], fmt).date()
        except ValueError:
            continue
    return None


def _reference_date(db):
    row = db.execute(text("""
        SELECT max(to_date(substring(match_date from 1 for 10), 'DD.MM.YYYY')) AS latest
        FROM xttv_matches
        WHERE match_date IS NOT NULL
    """)).mappings().first()
    latest = row['latest'] if row else None
    return latest or date.today()


def _cutoff(ref, years):
    return ref - timedelta(days=int(round(years * 365.25)))


def _empty_profile():
    return {
        'wins': 0, 'games': 0,
        'home_wins': 0, 'home_games': 0,
        'away_wins': 0, 'away_games': 0,
        'rc_rating': None, 'rc_trend': 0.0,
    }


def _win_rate(wins, games):
    return (wins + 5.0) / (games + 10.0)


def _rc_trend_from_snapshots(snapshots):
    if len(snapshots) < 2:
        return 0.0
    first, last = snapshots[0], snapshots[-1]
    delta = float(last['rc_rating']) - float(first['rc_rating'])
    days = max(1, (last['observed_at'].date() - first['observed_at'].date()).days)
    return max(-120.0, min(120.0, delta * 365.25 / days))


def _combined_strength(profile, side='overall'):
    rc = profile.get('rc_rating')
    if side == 'home':
        wins, games = profile.get('home_wins', 0), profile.get('home_games', 0)
    elif side == 'away':
        wins, games = profile.get('away_wins', 0), profile.get('away_games', 0)
    else:
        wins, games = profile.get('wins', 0), profile.get('games', 0)

    win_component = (_win_rate(wins, games) - 0.5) * 0.45
    trend_component = max(-0.08, min(0.08, profile.get('rc_trend', 0.0) / 150.0))

    if rc is not None:
        rc_component = (float(rc) - RC_BASELINE) / RC_SCALE
        return rc_component + win_component + trend_component

    return win_component * 2.2 + trend_component


def _logistic(delta, scale=4.0):
    return 1.0 / (1.0 + math.exp(-scale * delta))


def _matchup_probability(a, b, profiles, matchups, own_is_home=True):
    own_profile = profiles.get(a, _empty_profile())
    opp_profile = profiles.get(b, _empty_profile())
    if own_is_home:
        own_strength = _combined_strength(own_profile, 'home')
        opp_strength = _combined_strength(opp_profile, 'away')
    else:
        own_strength = _combined_strength(own_profile, 'away')
        opp_strength = _combined_strength(opp_profile, 'home')

    base = _logistic(own_strength - opp_strength)
    wins, games = matchups.get((a, b), (0, 0))
    if not games:
        return base
    direct = (wins + 1.5) / (games + 3.0)
    weight = min(H2H_MAX_WEIGHT, 0.35 + games / 6.0)
    return (1.0 - weight) * base + weight * direct


def _pair_probability(a, b, c, d, profiles):
    left = (_combined_strength(profiles.get(a, _empty_profile())) + _combined_strength(profiles.get(b, _empty_profile()))) / 2.0
    right = (_combined_strength(profiles.get(c, _empty_profile())) + _combined_strength(profiles.get(d, _empty_profile()))) / 2.0
    return _logistic(left - right)


def _doubles_pairs(order, profiles):
    ranked = sorted(order, key=lambda pid: _combined_strength(profiles.get(pid, _empty_profile())), reverse=True)
    return (ranked[0], ranked[1]), (ranked[2], ranked[3])


def _schedule_for_orientation(own_is_home):
    if own_is_home:
        return SINGLES_SCHEDULE
    return tuple((away_idx, home_idx) for home_idx, away_idx in SINGLES_SCHEDULE)


def _is_terminal_after_game(game_number, wins, losses):
    if game_number < 10:
        return False
    if game_number == TOTAL_GAMES:
        return True
    if wins >= WIN_TARGET:
        if wins == 8 and losses <= 1:
            return False
        if wins == 9 and losses == 0:
            return False
        return True
    if losses >= WIN_TARGET:
        if losses == 8 and wins <= 1:
            return False
        if losses == 9 and wins == 0:
            return False
        return True
    return False


def _team_result_distribution(probs):
    if len(probs) != TOTAL_GAMES:
        raise ValueError(f'expected {TOTAL_GAMES} game probabilities, got {len(probs)}')
    states = {(0, 0): 1.0}
    terminal_win = terminal_draw = terminal_loss = 0.0
    terminal_scores = defaultdict(float)

    def record_terminal(wins, losses, mass):
        nonlocal terminal_win, terminal_draw, terminal_loss
        terminal_scores[(wins, losses)] += mass
        if wins == losses == 7:
            terminal_draw += mass
        elif wins >= WIN_TARGET:
            terminal_win += mass
        elif losses >= WIN_TARGET:
            terminal_loss += mass
        else:
            raise RuntimeError(f'invalid terminal score {wins}:{losses}')

    for game_index, p in enumerate(probs, start=1):
        next_states = defaultdict(float)
        for (wins, losses), mass in states.items():
            nw, nl = wins + 1, losses
            mass_w = mass * p
            if _is_terminal_after_game(game_index, nw, nl):
                record_terminal(nw, nl, mass_w)
            else:
                next_states[(nw, nl)] += mass_w

            nw, nl = wins, losses + 1
            mass_l = mass * (1.0 - p)
            if _is_terminal_after_game(game_index, nw, nl):
                record_terminal(nw, nl, mass_l)
            else:
                next_states[(nw, nl)] += mass_l
        states = next_states

    for (wins, losses), mass in states.items():
        record_terminal(wins, losses, mass)

    expected_own = sum(w * mass for (w, _), mass in terminal_scores.items())
    expected_opp = sum(l * mass for (_, l), mass in terminal_scores.items())
    if terminal_scores:
        most_likely_own, most_likely_opp = max(terminal_scores.items(), key=lambda item: item[1])[0]
    else:
        most_likely_own, most_likely_opp = 0, 0

    return {
        'win': terminal_win,
        'draw': terminal_draw,
        'loss': terminal_loss,
        'expected_own_wins': expected_own,
        'expected_opponent_wins': expected_opp,
        'most_likely_own_wins': most_likely_own,
        'most_likely_opponent_wins': most_likely_opp,
    }


def _team_result_probabilities(probs):
    dist = _team_result_distribution(probs)
    return dist['win'], dist['draw'], dist['loss']


def _format_match_score_display(win_prob, expected_own, expected_opp):
    """Map expected game wins to TT match notation (8:x, x:8, 7:7, specials)."""
    own = float(expected_own or 0)
    opp = float(expected_opp or 0)

    if own >= 7.5 or (win_prob > 0.55 and own >= opp):
        opp_r = int(round(opp))
        if opp_r <= 0 and opp < 0.5:
            return '10:0'
        if opp_r <= 1 and opp < 1.5:
            return '9:1'
        return f'8:{max(2, min(6, opp_r))}'

    if opp >= 7.5 or (win_prob < 0.45 and opp >= own):
        own_r = int(round(own))
        if own_r >= 2:
            return f'{max(2, min(7, own_r))}:8'
        if own_r <= 0 and own < 0.5:
            return '0:10'
        if own_r <= 1 and own < 1.5:
            return '1:9'
        return f'{max(2, min(6, own_r))}:8'

    return '7:7'


def _position_index(position):
    value = str(position or '').strip().upper()
    return {'A': 0, 'B': 1, 'C': 2, 'D': 3, '1': 0, '2': 1, '3': 2, '4': 3}.get(value)


def _load_opponent_pool(db, team, ref_date):
    cutoff = _cutoff(ref_date, OPPONENT_POOL_YEARS)
    rows = db.execute(text("""
        SELECT DISTINCT mp.external_player_id::text AS player_id
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
          AND ((m.home_team = :team AND mp.side = 'home') OR (m.away_team = :team AND mp.side = 'away'))
    """), {'team': team, 'cutoff': cutoff}).scalars().all()
    return {str(pid) for pid in rows}


def _raw_team_lineup_scenarios(db, team, required_ids=None, ref_date=None, opponent_pool=None):
    ref_date = ref_date or _reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    opponent_pool = opponent_pool or _load_opponent_pool(db, team, ref_date)
    rows = db.execute(text("""
        SELECT m.id AS match_id, m.match_date, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))
          AND mp.external_player_id IS NOT NULL
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
        ORDER BY m.match_date DESC NULLS LAST, m.id DESC
    """), {'team': team, 'cutoff': stats_cutoff}).mappings()
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
            if not required.issubset(ids):
                continue
            if len(required) == 4 and ids != required:
                continue
        elif len(ids) != 4:
            continue
        if not ids.issubset(opponent_pool):
            continue
        order = [None] * 4; valid = True
        for pid in ids:
            idx = _position_index(by_id[pid]['position'])
            if idx is None or order[idx] is not None:
                valid = False; break
            order[idx] = pid
        if valid and all(order):
            counts[tuple(order)] += 1
    total = sum(counts.values())
    if not total:
        return [], names
    common = counts.most_common(24)
    top_total = sum(count for _, count in common)
    if not top_total:
        return [], names
    return [(count / top_total, order) for order, count in common], names


def _build_partial_opponent_scenarios(db, team, known_ids, opponent_pool, ref_date):
    known = {str(x) for x in known_ids}
    remaining = 4 - len(known)
    candidates = sorted(opponent_pool - known)
    if remaining < 0 or len(candidates) < remaining:
        return [], {}

    names = {}
    weighted = Counter()
    combos = list(combinations(candidates, remaining))
    if not combos:
        return [], {}

    combo_weight = 1.0 / len(combos)
    for extra in combos:
        full_set = list(known) + list(extra)
        sub_scenarios, sub_names = _raw_team_lineup_scenarios(db, team, full_set, ref_date, opponent_pool)
        names.update(sub_names)
        if sub_scenarios:
            for probability, order in sub_scenarios:
                weighted[order] += combo_weight * probability
        else:
            per_perm = combo_weight / 24.0
            for order in permutations(full_set):
                weighted[tuple(order)] += per_perm

    total = sum(weighted.values())
    if not total:
        return [], names
    common = weighted.most_common(24)
    top_total = sum(count for _, count in common)
    return [(count / top_total, order) for order, count in common], names


def _load_player_profiles(db, ids, ref_date):
    ids = [str(x) for x in ids]
    if not ids:
        return {}, {}, {}
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    params = {'ids': ids, 'cutoff': stats_cutoff}
    stats_stmt = text("""
        WITH games AS (
            SELECT hp.external_player_id::text AS player_id,
                   hp.name AS player_name,
                   'home'::text AS side,
                   CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS win
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
            UNION ALL
            SELECT ap.external_player_id::text, ap.name, 'away',
                   CASE WHEN split_part(trim(g.result),':',2)::int > split_part(trim(g.result),':',1)::int THEN 1 ELSE 0 END
            FROM match_games g
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND ap.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
        )
        SELECT player_id,
               max(player_name) AS player_name,
               sum(win) AS wins,
               count(*) AS games,
               sum(CASE WHEN side='home' THEN win ELSE 0 END) AS home_wins,
               sum(CASE WHEN side='home' THEN 1 ELSE 0 END) AS home_games,
               sum(CASE WHEN side='away' THEN win ELSE 0 END) AS away_wins,
               sum(CASE WHEN side='away' THEN 1 ELSE 0 END) AS away_games
        FROM games GROUP BY player_id
    """).bindparams(bindparam('ids', expanding=True))
    names = {}; profiles = {}
    for r in db.execute(stats_stmt, params).mappings():
        pid = str(r['player_id'])
        names[pid] = r['player_name']
        profiles[pid] = {
            'wins': int(r['wins'] or 0),
            'games': int(r['games'] or 0),
            'home_wins': int(r['home_wins'] or 0),
            'home_games': int(r['home_games'] or 0),
            'away_wins': int(r['away_wins'] or 0),
            'away_games': int(r['away_games'] or 0),
            'rc_rating': None,
            'rc_trend': 0.0,
        }

    name_stmt = text("""
        SELECT external_player_id::text AS player_id, max(name) AS player_name
        FROM match_players WHERE external_player_id::text IN :ids
        GROUP BY external_player_id
    """).bindparams(bindparam('ids', expanding=True))
    for r in db.execute(name_stmt, params).mappings():
        pid = str(r['player_id'])
        if r['player_name']:
            names[pid] = r['player_name']

    rc_stmt = text("""
        SELECT xp.external_player_id::text AS player_id,
               snap.rc_rating,
               snap.observed_at
        FROM xttv_players xp
        JOIN LATERAL (
            SELECT rc_rating, observed_at
            FROM player_rating_snapshots
            WHERE player_id = xp.id AND source = 'ratingscentral'
            ORDER BY observed_at DESC
            LIMIT 1
        ) snap ON true
        WHERE xp.external_player_id::text IN :ids
    """).bindparams(bindparam('ids', expanding=True))
    for r in db.execute(rc_stmt, params).mappings():
        pid = str(r['player_id'])
        profiles.setdefault(pid, _empty_profile())
        profiles[pid]['rc_rating'] = float(r['rc_rating']) if r['rc_rating'] is not None else None

    trend_cutoff = datetime.combine(stats_cutoff, datetime.min.time())
    trend_stmt = text("""
        SELECT xp.external_player_id::text AS player_id, s.observed_at, s.rc_rating
        FROM xttv_players xp
        JOIN player_rating_snapshots s ON s.player_id = xp.id AND s.source = 'ratingscentral'
        WHERE xp.external_player_id::text IN :ids
          AND s.observed_at >= :cutoff
        ORDER BY xp.external_player_id, s.observed_at
    """).bindparams(bindparam('ids', expanding=True))
    trend_rows = defaultdict(list)
    for r in db.execute(trend_stmt, {**params, 'cutoff': trend_cutoff}).mappings():
        trend_rows[str(r['player_id'])].append({'observed_at': r['observed_at'], 'rc_rating': r['rc_rating']})
    for pid, snapshots in trend_rows.items():
        profiles.setdefault(pid, _empty_profile())
        profiles[pid]['rc_trend'] = _rc_trend_from_snapshots(snapshots)

    h2h_stmt = text("""
        WITH base AS (
            SELECT hp.external_player_id::text AS home_id, ap.external_player_id::text AS away_id,
                   CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS home_win
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids AND ap.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
        )
        SELECT player_id, opponent_id, sum(win) AS wins, count(*) AS games
        FROM (
            SELECT home_id AS player_id, away_id AS opponent_id, home_win AS win FROM base
            UNION ALL
            SELECT away_id, home_id, 1 - home_win FROM base
        ) directed
        GROUP BY player_id, opponent_id
    """).bindparams(bindparam('ids', expanding=True))
    matchups = {}
    for r in db.execute(h2h_stmt, params).mappings():
        matchups[(str(r['player_id']), str(r['opponent_id']))] = (int(r['wins'] or 0), int(r['games'] or 0))
    return names, profiles, matchups


def _filter_scenarios(scenarios, opponent_pool):
    filtered = []
    for probability, order in scenarios:
        if set(order).issubset(opponent_pool):
            filtered.append((probability, order))
    if not filtered:
        return []
    total = sum(probability for probability, _ in filtered)
    if total <= 0:
        return filtered
    return [(probability / total, order) for probability, order in filtered]


def _load_analysis_data(own, opponent_team, actual):
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '5000ms'")); db.execute(text("SET lock_timeout = '500ms'"))
        own = [str(x) for x in own]; actual = None if actual is None else [str(x) for x in actual]
        ref_date = _reference_date(db)
        opponent_pool = _load_opponent_pool(db, opponent_team, ref_date)
        fallback_names = {}
        if actual is not None and len(actual) > 0:
            if len(actual) == 4:
                lineup_key = ','.join(sorted(actual))
                rows = list(db.execute(text("SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders WHERE lineup_key=:key ORDER BY appearances DESC LIMIT 24"), {'key': lineup_key}).mappings())
                total = sum(int(r['appearances'] or 0) for r in rows)
                if total:
                    scenarios = [(int(r['appearances']) / total, tuple(str(r[k]) for k in ('p1','p2','p3','p4'))) for r in rows]; source = 'known-opponent-historical-cache'
                else:
                    scenarios, fallback_names = _raw_team_lineup_scenarios(db, opponent_team, actual, ref_date, opponent_pool); source = 'known-opponent-historical-raw'
                    if not scenarios:
                        scenarios = [(1.0 / 24.0, tuple(order)) for order in permutations(actual)]; source = 'all-24-uniform-fallback'
            else:
                scenarios, fallback_names = _raw_team_lineup_scenarios(db, opponent_team, actual, ref_date, opponent_pool); source = 'known-opponent-historical-raw'
                if not scenarios:
                    scenarios, fallback_names = _build_partial_opponent_scenarios(db, opponent_team, actual, opponent_pool, ref_date); source = 'known-opponent-combination-fallback'
        else:
            rows = list(db.execute(text("SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders WHERE team=:team ORDER BY appearances DESC LIMIT 24"), {'team': opponent_team}).mappings())
            total = sum(int(r['appearances'] or 0) for r in rows)
            if total:
                scenarios = [(int(r['appearances']) / total, tuple(str(r[k]) for k in ('p1','p2','p3','p4'))) for r in rows]; source = 'predicted-historical-cache'
            else:
                scenarios, fallback_names = _raw_team_lineup_scenarios(db, opponent_team, None, ref_date, opponent_pool); source = 'predicted-historical-raw'
                if not scenarios:
                    return {}, {}, {}, [], source, ref_date, opponent_pool
        if source != 'all-24-uniform-fallback':
            scenarios = _filter_scenarios(scenarios, opponent_pool)
        relevant = set(own)
        for _, order in scenarios:
            relevant.update(order)
        ids = list(relevant)
        names, profiles, matchups = _load_player_profiles(db, ids, ref_date)
        names.update({k: v for k, v in fallback_names.items() if v})
        for pid in ids:
            profiles.setdefault(pid, _empty_profile()); names.setdefault(pid, f'Spieler {pid}')
        return names, profiles, matchups, scenarios, source, ref_date, opponent_pool
    except Exception as exc:
        raise RuntimeError(f'Analyse-Daten konnten nicht geladen werden: {type(exc).__name__}: {exc}') from exc
    finally:
        db.close()


def _build_matchup_table(relevant, profiles, matchups, own_is_home):
    return {
        (a, b): _matchup_probability(a, b, profiles, matchups, own_is_home=own_is_home)
        for a in relevant for b in relevant if a != b
    }


def _evaluate_lineups(own, scenarios, profiles, matchups, names, own_is_home, started):
    schedule = _schedule_for_orientation(own_is_home)
    relevant = set(own)
    for _, order in scenarios:
        relevant.update(order)
    matchup_p = _build_matchup_table(relevant, profiles, matchups, own_is_home)
    evaluated = []
    for own_order in permutations(own):
        expected_win = expected_draw = expected_loss = 0.0
        expected_own_wins = expected_opp_wins = 0.0
        own_doubles = _doubles_pairs(own_order, profiles)
        for scenario_probability, opp_order in scenarios:
            singles = [matchup_p.get((own_order[own_idx], opp_order[opp_idx]), 0.5) for own_idx, opp_idx in schedule]
            opp_doubles = _doubles_pairs(opp_order, profiles)
            doubles = [
                _pair_probability(*own_doubles[0], *opp_doubles[0], profiles),
                _pair_probability(*own_doubles[1], *opp_doubles[1], profiles),
            ]
            game_probs = singles[:4] + doubles[:1] + singles[4:8] + doubles[1:] + singles[8:]
            dist = _team_result_distribution(game_probs)
            expected_win += scenario_probability * dist['win']
            expected_draw += scenario_probability * dist['draw']
            expected_loss += scenario_probability * dist['loss']
            expected_own_wins += scenario_probability * dist['expected_own_wins']
            expected_opp_wins += scenario_probability * dist['expected_opponent_wins']
        evaluated.append({
            'own_player_ids': list(own_order),
            'players': [names.get(pid, f'Spieler {pid}') for pid in own_order],
            'team_win_probability': round(expected_win, 6),
            'team_draw_probability': round(expected_draw, 6),
            'team_loss_probability': round(expected_loss, 6),
            'expected_own_wins': round(expected_own_wins, 3),
            'expected_opponent_wins': round(expected_opp_wins, 3),
            'expected_score_display': _format_match_score_display(expected_win, expected_own_wins, expected_opp_wins),
        })
        if time.monotonic() - started > MAX_ANALYSIS_SECONDS:
            raise RuntimeError('Analysis exceeded the internal 5-second safety budget')
    return evaluated, matchup_p


def _merge_orientations(home_eval, away_eval):
    away_by_order = {tuple(item['own_player_ids']): item for item in away_eval}
    merged = []
    for home_item in home_eval:
        away_item = away_by_order[tuple(home_item['own_player_ids'])]
        merged.append({
            'own_player_ids': home_item['own_player_ids'],
            'players': home_item['players'],
            'team_win_probability': round((home_item['team_win_probability'] + away_item['team_win_probability']) / 2.0, 6),
            'team_draw_probability': round((home_item['team_draw_probability'] + away_item['team_draw_probability']) / 2.0, 6),
            'team_loss_probability': round((home_item['team_loss_probability'] + away_item['team_loss_probability']) / 2.0, 6),
            'expected_own_wins': round((home_item['expected_own_wins'] + away_item['expected_own_wins']) / 2.0, 3),
            'expected_opponent_wins': round((home_item['expected_opponent_wins'] + away_item['expected_opponent_wins']) / 2.0, 3),
            'expected_score_display': _format_match_score_display(
                (home_item['team_win_probability'] + away_item['team_win_probability']) / 2.0,
                (home_item['expected_own_wins'] + away_item['expected_own_wins']) / 2.0,
                (home_item['expected_opponent_wins'] + away_item['expected_opponent_wins']) / 2.0,
            ),
        })
    return merged


def _explain_recommendation(own_order, scenarios, matchup_p, profiles, names, evaluated, own_is_home=None):
    """Create a human-readable, model-grounded explanation for the top lineup."""
    weighted_games = [0.0] * TOTAL_GAMES
    weighted_double = [0.0, 0.0]
    position_rates = {pid: [0.0] * 4 for pid in own_order}

    schedule = _schedule_for_orientation(True if own_is_home is not False else False)

    for scenario_probability, opp_order in scenarios:
        singles = [matchup_p.get((own_order[own_idx], opp_order[opp_idx]), 0.5) for own_idx, opp_idx in schedule]
        own_doubles = _doubles_pairs(own_order, profiles)
        opp_doubles = _doubles_pairs(opp_order, profiles)
        doubles = [
            _pair_probability(*own_doubles[0], *opp_doubles[0], profiles),
            _pair_probability(*own_doubles[1], *opp_doubles[1], profiles),
        ]
        game_probs = singles[:4] + doubles[:1] + singles[4:8] + doubles[1:] + singles[8:]
        for i, p in enumerate(game_probs):
            weighted_games[i] += scenario_probability * p
        for i, p in enumerate(doubles):
            weighted_double[i] += scenario_probability * p

        for pid in own_order:
            for position in range(4):
                rate = sum(
                    matchup_p.get((pid, opp_order[opp_idx]), 0.5)
                    for own_idx, opp_idx in schedule if own_idx == position
                ) / 3.0
                position_rates[pid][position] += scenario_probability * rate

    player_current_rates = []
    for position, pid in enumerate(own_order):
        current = position_rates[pid][position]
        best_pos = max(range(4), key=lambda p: position_rates[pid][p])
        gain = position_rates[pid][best_pos] - current
        player_current_rates.append((pid, current, best_pos, gain))

    player_current_rates.sort(key=lambda x: x[1], reverse=True)
    strongest = player_current_rates[0]
    biggest_placement_gain = max(player_current_rates, key=lambda x: x[3])

    # Recalculate the best alternative from the already evaluated permutations.
    sorted_evaluated = sorted(evaluated, key=lambda x: x['team_win_probability'], reverse=True)
    best = sorted_evaluated[0]
    second = sorted_evaluated[1] if len(sorted_evaluated) > 1 else None
    margin = (best['team_win_probability'] - second['team_win_probability']) if second else 0.0

    # Most favorable and least favorable expected single game in the chosen order.
    single_game_indices = list(range(4)) + list(range(5, 9)) + list(range(10, 14))
    single_values = [(i + 1, weighted_games[i]) for i in single_game_indices]
    best_game = max(single_values, key=lambda x: x[1])
    worst_game = min(single_values, key=lambda x: x[1])

    bullets = []
    bullets.append(
        f"Die Reihenfolge ist optimal, weil sie die erwarteten Einzelspiel-Duelle über alle "
        f"historisch gewichteten gegnerischen Aufstellungen am besten verteilt."
    )
    if margin >= 0.005 and second:
        bullets.append(
            f"Gegenüber der zweitbesten Reihenfolge bringt sie rund {margin * 100:.1f} Prozentpunkte "
            f"mehr Mannschafts-Siegwahrscheinlichkeit ({best['team_win_probability'] * 100:.1f} % statt {second['team_win_probability'] * 100:.1f} %)."
        )
    elif second:
        bullets.append(
            f"Die ersten Aufstellungen liegen sehr eng beieinander: der Abstand zur zweitbesten "
            f"Reihenfolge beträgt nur {margin * 100:.1f} Prozentpunkte."
        )

    strongest_name = names.get(strongest[0], f'Spieler {strongest[0]}')
    bullets.append(
        f"{strongest_name} hat in seiner empfohlenen Position die höchste durchschnittliche "
        f"Einzelspielchance der vier ({strongest[1] * 100:.1f} %)."
    )

    gain_name = names.get(biggest_placement_gain[0], f'Spieler {biggest_placement_gain[0]}')
    if biggest_placement_gain[3] >= 0.01:
        bullets.append(
            f"Besonders wichtig ist die Positionierung von {gain_name}: seine aktuelle Position "
            f"ist gegenüber seiner rechnerisch besten anderen Position um {biggest_placement_gain[3] * 100:.1f} "
            f"Prozentpunkte günstiger als die entsprechende Platzierung in der übrigen Reihenfolge."
        )

    if weighted_double[0] >= 0.5 or weighted_double[1] >= 0.5:
        bullets.append(
            f"Die beiden Doppel werden ebenfalls berücksichtigt: erwartete Gewinnchance ca. "
            f"{weighted_double[0] * 100:.1f} % im ersten und {weighted_double[1] * 100:.1f} % im zweiten Doppel."
        )

    return {
        'headline': f"Warum diese Aufstellung? {names.get(own_order[0], own_order[0])} / {names.get(own_order[1], own_order[1])} / {names.get(own_order[2], own_order[2])} / {names.get(own_order[3], own_order[3])} erzielt im Modell die höchste Siegchance.",
        'bullets': bullets[:5],
        'detail': {
            'single_game_best_probability': round(best_game[1], 6),
            'single_game_worst_probability': round(worst_game[1], 6),
            'first_doubles_probability': round(weighted_double[0], 6),
            'second_doubles_probability': round(weighted_double[1], 6),
            'position_rates': [
                {
                    'player_id': pid,
                    'player_name': names.get(pid, f'Spieler {pid}'),
                    'recommended_position': pos + 1,
                    'recommended_single_probability': round(current, 6),
                    'best_position_by_singles': best_pos + 1,
                    'best_position_single_probability': round(position_rates[pid][best_pos], 6),
                }
                for pos, pid in enumerate(own_order)
                for _, current, best_pos, _ in [next(x for x in player_current_rates if x[0] == pid)]
            ],
        },
    }


def _rc_value(profile):
    rc = profile.get('rc_rating')
    return float(rc) if rc is not None else None


SINGLE_GAME_NUMBERS = (1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14)


def _expected_singles_wins_for_lineup(own_order, scenarios, matchup_p, own_is_home):
    """Expected singles wins per player (0–3) for the recommended lineup."""
    schedule = _schedule_for_orientation(bool(own_is_home))
    expected = {pid: 0.0 for pid in own_order}
    for scenario_probability, opp_order in scenarios:
        for own_idx, opp_idx in schedule:
            pid = own_order[own_idx]
            expected[pid] += scenario_probability * matchup_p.get((pid, opp_order[opp_idx]), 0.5)
    return expected


def _expected_singles_breakdown_for_lineup(own_order, scenarios, matchup_p, names, own_is_home):
    """Per-player singles matchups with weighted win probability (recommended lineup)."""
    schedule = _schedule_for_orientation(bool(own_is_home))
    breakdown = {pid: [] for pid in own_order}
    for game_idx, (own_idx, opp_idx) in enumerate(schedule):
        pid = own_order[own_idx]
        weighted_prob = 0.0
        opp_id = None
        for scenario_probability, opp_order in scenarios:
            opp_id = opp_order[opp_idx]
            weighted_prob += scenario_probability * matchup_p.get((pid, opp_id), 0.5)
        breakdown[pid].append({
            'game_number': SINGLE_GAME_NUMBERS[game_idx],
            'opponent_player_id': opp_id,
            'opponent_name': names.get(opp_id, f'Spieler {opp_id}'),
            'win_probability': round(weighted_prob, 3),
        })
    return breakdown


def _player_short_name(full_name: str) -> str:
    parts = (full_name or '').strip().split()
    return parts[-1] if parts else full_name


def _build_expected_singles_explanation(
    pid,
    position,
    breakdown,
    profile,
    matchups,
    names,
    exp_raw,
    exp_rounded,
):
    """Short German explanation for expected singles wins (Mehr Info)."""
    if not breakdown or exp_raw is None or exp_rounded is None:
        return None

    pos_label = 'ABCD'[position]
    game_parts = [
        f"Sp. {m['game_number']} vs {_player_short_name(m['opponent_name'])} (~{m['win_probability'] * 100:.0f} %)"
        for m in breakdown
    ]
    raw_txt = f"{exp_raw:.2f}".replace('.', ',')
    if abs(exp_raw - exp_rounded) >= 0.01:
        sum_part = f"Summe ~{raw_txt}, gerundet {exp_rounded} Einzel"
    else:
        sum_part = f"Erw. {exp_rounded} Einzel"

    text_parts = [f"In Position {pos_label} gegen {', '.join(game_parts)} — {sum_part}."]

    h2h_bits = []
    seen = set()
    for m in breakdown:
        opp_id = m['opponent_player_id']
        if opp_id in seen:
            continue
        wins, games = matchups.get((pid, opp_id), (0, 0))
        if games:
            own = _player_short_name(names.get(pid, pid))
            opp = _player_short_name(m['opponent_name'])
            h2h_bits.append(f"{own} {wins} : {games - wins} {opp}")
            seen.add(opp_id)
    if h2h_bits:
        text_parts.append(f"Direkt: {', '.join(h2h_bits)}.")

    rc = _rc_value(profile)
    wins = profile.get('wins', 0)
    games = profile.get('games', 0)
    if rc is not None:
        text_parts.append(f"RC {int(round(rc))}, Historie {wins}/{games} Einzel.")
    elif games:
        text_parts.append(f"Historie {wins}/{games} Einzel.")

    return ' '.join(text_parts)


def _build_info_summary(
    own, scenarios, profiles, names, matchups, recommendation, evaluated, explanation,
    opponent_team, ref_date, opponent_pool, source, orientation_note, matchup_p=None, own_is_home=None,
):
    own_order = recommendation['own_player_ids']
    expected_singles = {}
    singles_breakdown = {}
    if matchup_p is not None and own_is_home is not None:
        expected_singles = _expected_singles_wins_for_lineup(own_order, scenarios, matchup_p, own_is_home)
        singles_breakdown = _expected_singles_breakdown_for_lineup(own_order, scenarios, matchup_p, names, own_is_home)
    elif matchup_p is not None and own_is_home is None:
        home = _expected_singles_wins_for_lineup(own_order, scenarios, matchup_p, True)
        away_matchup = _build_matchup_table(
            set(own_order) | {pid for _, order in scenarios for pid in order},
            profiles,
            matchups,
            False,
        )
        away = _expected_singles_wins_for_lineup(own_order, scenarios, away_matchup, False)
        expected_singles = {pid: (home[pid] + away[pid]) / 2.0 for pid in own_order}
        home_breakdown = _expected_singles_breakdown_for_lineup(own_order, scenarios, matchup_p, names, True)
        away_breakdown = _expected_singles_breakdown_for_lineup(own_order, scenarios, away_matchup, names, False)
        singles_breakdown = {
            pid: [
                {
                    **home_breakdown[pid][i],
                    'win_probability': round((home_breakdown[pid][i]['win_probability'] + away_breakdown[pid][i]['win_probability']) / 2.0, 3),
                }
                for i in range(len(home_breakdown[pid]))
            ]
            for pid in own_order
        }

    own_players = []
    own_rc_values = []
    for position, pid in enumerate(own_order):
        profile = profiles.get(pid, _empty_profile())
        rc = _rc_value(profile)
        exp_singles = expected_singles.get(pid)
        exp_rounded = int(round(exp_singles)) if exp_singles is not None else None
        exp_raw = round(exp_singles, 2) if exp_singles is not None else None
        breakdown = singles_breakdown.get(pid, [])
        own_players.append({
            'player_id': pid,
            'player_name': names.get(pid, f'Spieler {pid}'),
            'lineup_position': 'ABCD'[position],
            'rc_rating': round(rc, 1) if rc is not None else None,
            'rc_trend': round(profile.get('rc_trend', 0.0), 1),
            'singles_wins': profile.get('wins', 0),
            'singles_games': profile.get('games', 0),
            'expected_singles_wins': exp_rounded,
            'expected_singles_wins_raw': exp_raw,
            'expected_singles_matchups': breakdown,
            'expected_singles_explanation': _build_expected_singles_explanation(
                pid, position, breakdown, profile, matchups, names, exp_raw, exp_rounded,
            ),
        })
        if rc is not None:
            own_rc_values.append(rc)

    opponent_ids = set()
    weighted_opp_rc = 0.0
    weighted_opp_rc_mass = 0.0
    for probability, order in scenarios:
        opponent_ids.update(order)
        lineup_rc = [_rc_value(profiles.get(pid, _empty_profile())) for pid in order]
        lineup_rc = [v for v in lineup_rc if v is not None]
        if lineup_rc:
            weighted_opp_rc += probability * sum(lineup_rc)
            weighted_opp_rc_mass += probability

    top_order = scenarios[0][1] if scenarios else tuple()
    top_rc_values = [_rc_value(profiles.get(pid, _empty_profile())) for pid in top_order]
    top_rc_values = [v for v in top_rc_values if v is not None]

    h2h_pairs = []
    for a in own:
        for b in opponent_ids:
            wins, games = matchups.get((a, b), (0, 0))
            if games:
                h2h_pairs.append({
                    'own_player_id': a,
                    'own_player_name': names.get(a, a),
                    'opponent_player_id': b,
                    'opponent_player_name': names.get(b, b),
                    'wins': wins,
                    'games': games,
                })

    margin_pp = 0.0
    if len(evaluated) > 1:
        margin_pp = round((evaluated[0]['team_win_probability'] - evaluated[1]['team_win_probability']) * 100, 2)

    own_sum = round(sum(own_rc_values), 1) if own_rc_values else None
    own_avg = round(sum(own_rc_values) / len(own_rc_values), 1) if own_rc_values else None
    top_sum = round(sum(top_rc_values), 1) if top_rc_values else None
    top_avg = round(sum(top_rc_values) / len(top_rc_values), 1) if top_rc_values else None
    weighted_avg = round(weighted_opp_rc / weighted_opp_rc_mass, 1) if weighted_opp_rc_mass else None

    detail = (explanation or {}).get('detail') or {}
    return {
        'own_rc_sum': own_sum,
        'own_rc_avg': own_avg,
        'own_rc_count': len(own_rc_values),
        'opponent_top_lineup_rc_sum': top_sum,
        'opponent_top_lineup_rc_avg': top_avg,
        'opponent_weighted_rc_avg': weighted_avg,
        'rc_gap_vs_top_lineup': round(own_avg - top_avg, 1) if own_avg is not None and top_avg is not None else None,
        'own_players': own_players,
        'opponent_team': opponent_team,
        'top_lineup_margin_pp': margin_pp,
        'h2h_pairs_with_data': len(h2h_pairs),
        'h2h_pairs': sorted(h2h_pairs, key=lambda x: -x['games'])[:8],
        'expected_first_doubles_probability': detail.get('first_doubles_probability'),
        'expected_second_doubles_probability': detail.get('second_doubles_probability'),
        'reference_date': ref_date.isoformat(),
        'stats_window_years': STATS_YEARS,
        'opponent_pool_years': OPPONENT_POOL_YEARS,
        'opponent_pool_size': len(opponent_pool),
        'scenario_variants': len(scenarios),
        'opponent_set_source': source,
        'orientation': orientation_note,
    }


def analyze_lineup(own_player_ids, opponent_team, actual_opponent_ids=None, opponent_limit=24, own_is_home=None):
    started = time.monotonic(); own = [str(x) for x in own_player_ids]
    if len(own) != 4 or len(set(own)) != 4:
        raise ValueError('exactly four different own_player_ids are required')
    if not opponent_team:
        raise ValueError('opponent_team is required')
    actual = None if actual_opponent_ids is None else [str(x) for x in actual_opponent_ids]
    if actual is not None:
        if len(actual) > 4 or len(set(actual)) != len(actual):
            raise ValueError('up to four different known_opponent_ids are allowed')
        if not actual:
            actual = None

    names, profiles, matchups, scenarios, source, ref_date, opponent_pool = _load_analysis_data(own, opponent_team, actual)
    if not scenarios:
        return {'ok': True, 'phase': 'B' if actual else 'A', 'recommendations': [], 'warnings': [f'Keine passende Gegner-Aufstellung für {opponent_team} gefunden.']}

    if own_is_home is None:
        home_eval, home_matchups = _evaluate_lineups(own, scenarios, profiles, matchups, names, True, started)
        away_eval, _ = _evaluate_lineups(own, scenarios, profiles, matchups, names, False, started)
        evaluated = _merge_orientations(home_eval, away_eval)
        matchup_p = home_matchups
        orientation_note = 'home-and-away-averaged'
    else:
        evaluated, matchup_p = _evaluate_lineups(own, scenarios, profiles, matchups, names, bool(own_is_home), started)
        orientation_note = 'home' if own_is_home else 'away'

    evaluated.sort(key=lambda x: (-x['team_win_probability'], x['own_player_ids']))
    for rank, item in enumerate(evaluated, 1):
        item['rank'] = rank

    recommendation = evaluated[0]
    explanation = _explain_recommendation(recommendation['own_player_ids'], scenarios, matchup_p, profiles, names, evaluated, own_is_home)
    opponent_predictions = [
        {'player_ids': list(order), 'players': [{'id': p, 'name': names.get(p, f'Spieler {p}')} for p in order], 'probability': round(probability, 6)}
        for probability, order in scenarios
    ]
    elapsed = time.monotonic() - started
    info_summary = _build_info_summary(
        own, scenarios, profiles, names, matchups, recommendation, evaluated, explanation,
        opponent_team, ref_date, opponent_pool, source, orientation_note,
        matchup_p=matchup_p, own_is_home=own_is_home,
    )

    return {
        'ok': True,
        'phase': 'B' if actual else 'A',
        'known_opponent_ids': actual or [],
        'known_opponent_count': len(actual or []),
        'own_is_home': own_is_home,
        'opponent_team': opponent_team,
        'own_player_ids': own,
        'opponent_set_source': source,
        'recommendation': recommendation,
        'recommendations': evaluated,
        'explanation': explanation,
        'info_summary': info_summary,
        'opponent_predictions': opponent_predictions,
        'most_likely_opponent': opponent_predictions[0] if opponent_predictions else None,
        'model': {
            'version': MODEL_VERSION,
            'win_target': WIN_TARGET,
            'single_games': SINGLE_GAMES,
            'doubles_games': DOUBLE_GAMES,
            'total_games': TOTAL_GAMES,
            'mandatory_games': 10,
            'draw_score': '7:7',
            'special_results': ['9:1', '10:0'],
            'singles_schedule': 'D-2, A-3, C-4, B-1, D, A-2, D-3, C-1, B-4, D, A-1, B-2, C-3, D-4',
            'doubles_pairing': 'game 5: two strongest together; game 10: two weakest together',
            'strength_priority': 'RC rating, then wins/games (3y), then RC trend (ascending stronger)',
            'h2h_weight': f'up to {int(H2H_MAX_WEIGHT * 100)}% when direct singles exist',
            'home_away': 'separate home/away singles records; orientation averaged unless own_is_home is set',
            'opponent_lineups': f'historical position orders from last {STATS_YEARS} years; player pool last {OPPONENT_POOL_YEARS} years',
            'player_stats': f'XTTV singles + RC snapshots, last {STATS_YEARS} years',
            'orientation': orientation_note,
        },
        'data_quality': {
            'scenario_variants': len(scenarios),
            'own_orders_evaluated': 24,
            'reference_date': ref_date.isoformat(),
            'stats_window_years': STATS_YEARS,
            'opponent_pool_years': OPPONENT_POOL_YEARS,
            'opponent_pool_size': len(opponent_pool),
            'runtime_seconds': round(elapsed, 4),
            'runtime_data_source': 'rc-profiles-plus-lineup-history',
            'missing_player_stats_use_neutral_prior': False,
            'position_probabilities_observed': source != 'all-24-uniform-fallback',
        },
    }
