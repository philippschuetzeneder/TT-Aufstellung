from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from itertools import combinations, permutations
import math
import time
from sqlalchemy import text, bindparam
from .db import SessionLocal
from .doubles_service import (
    choose_opponent_doubles_on_games,
    doubles_matchup_probability,
    load_doubles_pair_stats,
    predict_opponent_doubles_lineup,
)

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
TREND_YEARS = 1
TREND_MAX_COMPONENT = 0.12
SPIELTYP_MAX_COMPONENT = TREND_MAX_COMPONENT
SPIELTYP_MIN_GAMES = 2
TREND_FULL_RC_DELTA = 80.0
# RC is the primary current-strength signal.  The singles record is only a
# deliberately small corroborating signal; trend gets a comparable bounded
# contribution so recent form can matter without dominating the model.
RC_BASELINE = 1400.0
RC_SCALE = 400.0
RC_COMPONENT_WEIGHT = 0.75
SINGLES_RECORD_WEIGHT = 0.18
HOME_AWAY_MAX_COMPONENT = 0.08
HOME_AWAY_MIN_GAMES = 8
HOME_AWAY_MIN_OVERALL_GAMES = 12
HOME_AWAY_COMPONENT_SCALE = 0.32
H2H_MAX_WEIGHT = 0.85
MODEL_VERSION = 'rc-h2h-homeaway-v27-balanced-signals'

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
        'rc_rating': None, 'rc_trend': 0.0, 'trend_component': 0.0,
        'spieltyp': None, 'style_matchups': {}, 'style_component': 0.0,
    }


def _weighted_rc_momentum(snapshots):
    """Recency-weighted sum of RC point changes (recent updates weigh more)."""
    if len(snapshots) < 2:
        return 0.0
    ref = snapshots[-1]['observed_at']
    total = 0.0
    for index in range(1, len(snapshots)):
        prev, cur = snapshots[index - 1], snapshots[index]
        delta = float(cur['rc_rating']) - float(prev['rc_rating'])
        days_ago = max(0, (ref - cur['observed_at']).days)
        weight = max(0.15, 1.0 - 0.85 * days_ago / 365.25)
        total += delta * weight
    return total


def _recent_rc_delta(snapshots, max_steps: int = 3) -> float:
    """RC rating change across the last N RC observations."""
    if len(snapshots) < 2:
        return 0.0
    window = min(len(snapshots), max_steps + 1)
    subset = snapshots[-window:]
    return float(subset[-1]['rc_rating']) - float(subset[0]['rc_rating'])


def _recent_singles_all_3_0(recent_singles: list[dict]) -> bool:
    if len(recent_singles) < 3:
        return False
    for row in recent_singles[:3]:
        if int(row['own_score']) != 3 or int(row['opp_score']) != 0:
            return False
    return True


def _compute_trend_metrics(snapshots_1y: list[dict], recent_singles: list[dict]) -> tuple[float, float]:
    """Return (rc_trend display value, trend_component for strength)."""
    if not snapshots_1y:
        return 0.0, 0.0

    momentum = _weighted_rc_momentum(snapshots_1y)
    recent_delta = _recent_rc_delta(snapshots_1y, 3)

    if _recent_singles_all_3_0(recent_singles):
        return momentum, TREND_MAX_COMPONENT
    if recent_delta >= TREND_FULL_RC_DELTA:
        return momentum, TREND_MAX_COMPONENT
    if recent_delta <= -TREND_FULL_RC_DELTA:
        return momentum, -TREND_MAX_COMPONENT

    component = max(
        -TREND_MAX_COMPONENT,
        min(TREND_MAX_COMPONENT, momentum / TREND_FULL_RC_DELTA * TREND_MAX_COMPONENT),
    )
    return momentum, component


def _rc_trend_from_snapshots(snapshots):
    """Recency-weighted RC momentum (1-year window expected by caller)."""
    return _weighted_rc_momentum(snapshots)


def _win_rate(wins, games):
    return (wins + 5.0) / (games + 10.0)


def _combined_strength(profile, side='overall'):
    rc = profile.get('rc_rating')
    overall_wins = profile.get('wins', 0)
    overall_games = profile.get('games', 0)
    win_component = (_win_rate(overall_wins, overall_games) - 0.5) * SINGLES_RECORD_WEIGHT
    trend_component = float(profile.get('trend_component', 0.0))
    venue_component = 0.0
    if (
        side in ('home', 'away')
        and overall_games >= HOME_AWAY_MIN_OVERALL_GAMES
    ):
        if side == 'home':
            venue_wins, venue_games = profile.get('home_wins', 0), profile.get('home_games', 0)
        else:
            venue_wins, venue_games = profile.get('away_wins', 0), profile.get('away_games', 0)
        if venue_games >= HOME_AWAY_MIN_GAMES:
            venue_delta = _win_rate(venue_wins, venue_games) - _win_rate(overall_wins, overall_games)
            venue_component = max(
                -HOME_AWAY_MAX_COMPONENT,
                min(HOME_AWAY_MAX_COMPONENT, venue_delta * HOME_AWAY_COMPONENT_SCALE),
            )

    if rc is not None:
        rc_component = ((float(rc) - RC_BASELINE) / RC_SCALE) * RC_COMPONENT_WEIGHT
        return rc_component + win_component + trend_component + venue_component

    return win_component + trend_component + venue_component


def _style_match_rate(profile, opponent_style):
    if not opponent_style:
        return None
    wins, games = profile.get('style_matchups', {}).get(opponent_style, (0, 0))
    if games < SPIELTYP_MIN_GAMES:
        return None
    return (wins + 1.5) / (games + 3.0)


def _style_component(own_profile, opp_profile):
    opp_style = opp_profile.get('spieltyp')
    if not opp_style:
        return 0.0
    rate = _style_match_rate(own_profile, opp_style)
    if rate is None:
        return 0.0
    baseline = _win_rate(own_profile.get('wins', 0), own_profile.get('games', 0))
    delta = rate - baseline
    scale = SPIELTYP_MAX_COMPONENT / 0.25
    return max(-SPIELTYP_MAX_COMPONENT, min(SPIELTYP_MAX_COMPONENT, delta * scale))


def _logistic(delta, scale=4.0):
    return 1.0 / (1.0 + math.exp(-scale * delta))


def _matchup_probability(a, b, profiles, matchups, own_is_home=True, use_spieltyp=False):
    own_profile = profiles.get(a, _empty_profile())
    opp_profile = profiles.get(b, _empty_profile())
    if own_is_home:
        own_strength = _combined_strength(own_profile, 'home')
        opp_strength = _combined_strength(opp_profile, 'away')
    else:
        own_strength = _combined_strength(own_profile, 'away')
        opp_strength = _combined_strength(opp_profile, 'home')

    if use_spieltyp:
        own_strength += _style_component(own_profile, opp_profile)
        opp_strength += _style_component(opp_profile, own_profile)

    base = _logistic(own_strength - opp_strength)
    wins, games = matchups.get((a, b), (0, 0))
    if not games:
        return base
    direct = (wins + 1.5) / (games + 3.0)
    weight = min(H2H_MAX_WEIGHT, 0.35 + games / 6.0)
    return (1.0 - weight) * base + weight * direct


def _pair_combined_strength(p1, p2, profiles):
    return (
        _combined_strength(profiles.get(p1, _empty_profile()))
        + _combined_strength(profiles.get(p2, _empty_profile()))
    ) / 2.0


def _default_double_pairs(player_ids, profiles):
    ranked = sorted(player_ids, key=lambda pid: _combined_strength(profiles.get(pid, _empty_profile())), reverse=True)
    return (tuple(ranked[:2]), tuple(ranked[2:4]))


def _normalize_own_double_pairs(own, own_double_pairs, profiles):
    own = [str(x) for x in own]
    if not own_double_pairs:
        return _default_double_pairs(own, profiles)
    if len(own_double_pairs) != 2:
        raise ValueError('own_double_pairs must contain exactly two pairs')
    pair_a = tuple(str(x) for x in own_double_pairs[0])
    pair_b = tuple(str(x) for x in own_double_pairs[1])
    if len(pair_a) != 2 or len(pair_b) != 2 or len(set(pair_a + pair_b)) != 4 or set(pair_a + pair_b) != set(own):
        raise ValueError('own_double_pairs must partition the four own_player_ids into two pairs')
    return pair_a, pair_b


def _resolve_own_doubles_for_games(pair_a, pair_b, stronger_doubles_on=5, stronger_double_pair=1):
    stronger_doubles_on = int(stronger_doubles_on)
    stronger_double_pair = int(stronger_double_pair)
    if stronger_doubles_on not in (5, 10):
        raise ValueError('stronger_doubles_on must be 5 or 10')
    if stronger_double_pair not in (1, 2):
        raise ValueError('stronger_double_pair must be 1 or 2')
    strong = pair_a if stronger_double_pair == 1 else pair_b
    weak = pair_b if stronger_double_pair == 1 else pair_a
    if stronger_doubles_on == 10:
        return weak, strong
    return strong, weak


def _doubles_probs_for_scenario(opp_order, pair_a, pair_b, profiles, doubles_stats, stronger_double_pair, fixed_game_pairs=None):
    """Precompute doubles win probs for both own placements; depends on opp lineup, not own singles order."""
    opp_game5, opp_game10 = choose_opponent_doubles_on_games(list(opp_order), doubles_stats, profiles, _combined_strength)
    if fixed_game_pairs is not None:
        game5_on_5, game10_on_5 = fixed_game_pairs
        game5_on_10, game10_on_10 = fixed_game_pairs
    else:
        game5_on_5, game10_on_5 = _resolve_own_doubles_for_games(pair_a, pair_b, 5, stronger_double_pair)
        game5_on_10, game10_on_10 = _resolve_own_doubles_for_games(pair_a, pair_b, 10, stronger_double_pair)
    doubles5 = (
        doubles_matchup_probability(game5_on_5, opp_game5, doubles_stats, profiles, _combined_strength, _logistic),
        doubles_matchup_probability(game10_on_5, opp_game10, doubles_stats, profiles, _combined_strength, _logistic),
    )
    doubles10 = (
        doubles_matchup_probability(game5_on_10, opp_game5, doubles_stats, profiles, _combined_strength, _logistic),
        doubles_matchup_probability(game10_on_10, opp_game10, doubles_stats, profiles, _combined_strength, _logistic),
    )
    return doubles5, doubles10


def _build_scenario_doubles_cache(scenarios, pair_a, pair_b, profiles, doubles_stats, stronger_double_pair, fixed_game_pairs=None):
    opp_cache = {}
    rows = []
    for scenario_probability, opp_order in scenarios:
        key = tuple(opp_order)
        if key not in opp_cache:
            opp_cache[key] = _doubles_probs_for_scenario(
                opp_order, pair_a, pair_b, profiles, doubles_stats, stronger_double_pair, fixed_game_pairs,
            )
        rows.append((scenario_probability, opp_order, opp_cache[key][0], opp_cache[key][1]))
    return rows


def _scenario_match_outcome(
    own_order, opp_order, schedule, matchup_p, pair_a, pair_b, profiles, doubles_stats,
    stronger_doubles_on, stronger_double_pair, doubles5=None, doubles10=None,
    fixed_game_pairs=None,
):
    singles = [matchup_p.get((own_order[own_idx], opp_order[opp_idx]), 0.5) for own_idx, opp_idx in schedule]
    if doubles5 is None or doubles10 is None:
        doubles5, doubles10 = _doubles_probs_for_scenario(
            opp_order, pair_a, pair_b, profiles, doubles_stats, stronger_double_pair, fixed_game_pairs,
        )
    doubles = doubles5 if int(stronger_doubles_on) == 5 else doubles10
    game_probs = _build_game_probs(singles, doubles)
    dist = _team_result_distribution(game_probs)
    return dist, game_probs, list(doubles)


def _build_game_probs(singles, doubles):
    """Build the official game order, including both doubles games."""
    return list(singles[:4]) + [doubles[0]] + list(singles[4:8]) + [doubles[1]] + list(singles[8:])


def _game_play_probabilities(probs):
    """Probability that each game is reached before applying its outcome."""
    states = {(0, 0): 1.0}
    played = []
    for game_index, p in enumerate(probs, start=1):
        played.append(sum(states.values()))
        next_states = defaultdict(float)
        for (wins, losses), mass in states.items():
            mass_w = mass * p
            if not _is_terminal_after_game(game_index, wins + 1, losses):
                next_states[(wins + 1, losses)] += mass_w
            mass_l = mass * (1.0 - p)
            if not _is_terminal_after_game(game_index, wins, losses + 1):
                next_states[(wins, losses + 1)] += mass_l
        states = next_states
    return played


def _evaluate_lineup_for_perm(own_order, scenario_cache, matchup_p, schedule):
    agg5 = _empty_match_agg()
    agg10 = _empty_match_agg()
    for scenario_probability, opp_order, doubles5, doubles10 in scenario_cache:
        singles = [matchup_p.get((own_order[own_idx], opp_order[opp_idx]), 0.5) for own_idx, opp_idx in schedule]
        for agg, doubles in ((agg5, doubles5), (agg10, doubles10)):
            game_probs = _build_game_probs(singles, doubles)
            _accumulate_match_agg(agg, _team_result_distribution(game_probs), scenario_probability)
    win5 = agg5['win']
    win10 = agg10['win']
    recommended = 5 if win5 >= win10 else 10
    return recommended, (agg5 if recommended == 5 else agg10), win5, win10, agg5, agg10


def _check_analysis_budget(started):
    if time.monotonic() - started > MAX_ANALYSIS_SECONDS:
        raise RuntimeError('Analysis exceeded the internal 5-second safety budget')


def _empty_match_agg():
    return {'win': 0.0, 'draw': 0.0, 'loss': 0.0, 'expected_own_wins': 0.0, 'expected_opponent_wins': 0.0}


def _accumulate_match_agg(target, dist, weight):
    target['win'] += weight * dist['win']
    target['draw'] += weight * dist['draw']
    target['loss'] += weight * dist['loss']
    target['expected_own_wins'] += weight * dist['expected_own_wins']
    target['expected_opponent_wins'] += weight * dist['expected_opponent_wins']


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


def _known_quartet_lineup_scenarios(db, team, player_ids, ref_date=None):
    """Load only historical matches containing exactly the known quartet."""
    ref_date = ref_date or _reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    ids = [str(x) for x in player_ids]
    bind_names = [f'known_id_{i}' for i in range(len(ids))]
    id_params = {name: value for name, value in zip(bind_names, ids)}
    placeholders = ','.join(f':{name}' for name in bind_names)
    rows = db.execute(text(f"""
        SELECT m.id AS match_id, m.match_date, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))
          AND mp.external_player_id IS NOT NULL
          AND mp.external_player_id::text IN ({placeholders})
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
        ORDER BY m.match_date DESC NULLS LAST, m.id DESC
    """), {'team': team, 'cutoff': stats_cutoff, **id_params}).mappings()
    matches = defaultdict(list)
    names = {}
    required = set(ids)
    for row in rows:
        matches[row['match_id']].append(row)
        names.setdefault(str(row['player_id']), row['player_name'])

    counts = Counter()
    for players in matches.values():
        by_id = {str(row['player_id']): row for row in players}
        if set(by_id) != required:
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
    return [(count / total, order) for order, count in counts.most_common(24)], names


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
            'trend_component': 0.0,
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
            WHERE player_id = xp.id
              AND source = 'ratingscentral'
              AND observed_at < :ref_date_exclusive
            ORDER BY observed_at DESC
            LIMIT 1
        ) snap ON true
        WHERE xp.external_player_id::text IN :ids
    """).bindparams(bindparam('ids', expanding=True))
    rc_params = {**params, 'ref_date_exclusive': datetime.combine(ref_date + timedelta(days=1), datetime.min.time())}
    for r in db.execute(rc_stmt, rc_params).mappings():
        pid = str(r['player_id'])
        profiles.setdefault(pid, _empty_profile())
        profiles[pid]['rc_rating'] = float(r['rc_rating']) if r['rc_rating'] is not None else None

    trend_cutoff = datetime.combine(_cutoff(ref_date, TREND_YEARS), datetime.min.time())
    trend_stmt = text("""
        SELECT xp.external_player_id::text AS player_id, s.observed_at, s.rc_rating
        FROM xttv_players xp
        JOIN player_rating_snapshots s ON s.player_id = xp.id AND s.source = 'ratingscentral'
        WHERE xp.external_player_id::text IN :ids
          AND s.observed_at >= :cutoff
          AND s.observed_at < :ref_date_exclusive
        ORDER BY xp.external_player_id, s.observed_at
    """).bindparams(bindparam('ids', expanding=True))
    trend_rows = defaultdict(list)
    for r in db.execute(
        trend_stmt,
        {
            **params,
            'cutoff': trend_cutoff,
            'ref_date_exclusive': datetime.combine(ref_date + timedelta(days=1), datetime.min.time()),
        },
    ).mappings():
        trend_rows[str(r['player_id'])].append({'observed_at': r['observed_at'], 'rc_rating': r['rc_rating']})

    recent_singles_stmt = text("""
        WITH all_singles AS (
            SELECT hp.external_player_id::text AS player_id,
                   to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') AS match_day,
                   split_part(trim(g.result),':',1)::int AS own_score,
                   split_part(trim(g.result),':',2)::int AS opp_score
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
            UNION ALL
            SELECT ap.external_player_id::text,
                   to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'),
                   split_part(trim(g.result),':',2)::int,
                   split_part(trim(g.result),':',1)::int
            FROM match_games g
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND ap.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
        ),
        ranked AS (
            SELECT player_id, own_score, opp_score, match_day,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY match_day DESC) AS rn
            FROM all_singles
        )
        SELECT player_id, own_score, opp_score, match_day
        FROM ranked
        WHERE rn <= 3
        ORDER BY player_id, match_day DESC
    """).bindparams(bindparam('ids', expanding=True))
    recent_singles_rows = defaultdict(list)
    for r in db.execute(recent_singles_stmt, params).mappings():
        recent_singles_rows[str(r['player_id'])].append({
            'own_score': int(r['own_score']),
            'opp_score': int(r['opp_score']),
            'match_day': r['match_day'],
        })

    for pid, snapshots in trend_rows.items():
        profiles.setdefault(pid, _empty_profile())
        momentum, component = _compute_trend_metrics(snapshots, recent_singles_rows.get(pid, []))
        profiles[pid]['rc_trend'] = momentum
        profiles[pid]['trend_component'] = component

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


def _augment_profiles_spieltyp(db, ids, profiles, ref_date):
    ids = [str(x) for x in ids]
    if not ids:
        return
    params = {'ids': ids, 'cutoff': _cutoff(ref_date, STATS_YEARS)}
    spieltyp_stmt = text("""
        SELECT external_player_id::text AS player_id, spieltyp
        FROM xttv_players
        WHERE external_player_id::text IN :ids
    """).bindparams(bindparam('ids', expanding=True))
    for r in db.execute(spieltyp_stmt, params).mappings():
        pid = str(r['player_id'])
        profiles.setdefault(pid, _empty_profile())
        profiles[pid]['spieltyp'] = r['spieltyp']

    style_stmt = text("""
        WITH base AS (
            SELECT hp.external_player_id::text AS player_id,
                   xp_opp.spieltyp AS opp_style,
                   CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS win
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN match_players op ON op.match_id=g.match_id AND op.side='away' AND op.position=g.away_position
            LEFT JOIN xttv_players xp_opp ON xp_opp.external_player_id = op.external_player_id::text
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids
              AND xp_opp.spieltyp IS NOT NULL
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
            UNION ALL
            SELECT ap.external_player_id::text,
                   xp_opp.spieltyp,
                   CASE WHEN split_part(trim(g.result),':',2)::int > split_part(trim(g.result),':',1)::int THEN 1 ELSE 0 END
            FROM match_games g
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN match_players op ON op.match_id=g.match_id AND op.side='home' AND op.position=g.home_position
            LEFT JOIN xttv_players xp_opp ON xp_opp.external_player_id = op.external_player_id::text
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND ap.external_player_id::text IN :ids
              AND xp_opp.spieltyp IS NOT NULL
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
        )
        SELECT player_id, opp_style, sum(win) AS wins, count(*) AS games
        FROM base
        GROUP BY player_id, opp_style
    """).bindparams(bindparam('ids', expanding=True))
    style_rows = defaultdict(dict)
    for r in db.execute(style_stmt, params).mappings():
        pid = str(r['player_id'])
        style_rows[pid][str(r['opp_style'])] = (int(r['wins'] or 0), int(r['games'] or 0))

    for pid in ids:
        profile = profiles.setdefault(pid, _empty_profile())
        profile['style_matchups'] = style_rows.get(pid, {})
        profile['style_component'] = 0.0


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


def _load_analysis_data(own, opponent_team, actual, use_spieltyp=False):
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
                    scenarios, fallback_names = _known_quartet_lineup_scenarios(db, opponent_team, actual, ref_date)
                    source = 'known-opponent-historical-raw'
                    if not scenarios:
                        # Keep a neutral fallback only when no usable historical
                        # position order exists for this exact quartet.
                        scenarios = [(1.0 / 24.0, tuple(order)) for order in permutations(actual)]
                        source = 'all-24-uniform-fallback'
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
        if use_spieltyp:
            _augment_profiles_spieltyp(db, ids, profiles, ref_date)
        names.update({k: v for k, v in fallback_names.items() if v})
        for pid in ids:
            profiles.setdefault(pid, _empty_profile()); names.setdefault(pid, f'Spieler {pid}')
        return names, profiles, matchups, scenarios, source, ref_date, opponent_pool
    except Exception as exc:
        raise RuntimeError(f'Analyse-Daten konnten nicht geladen werden: {type(exc).__name__}: {exc}') from exc
    finally:
        db.close()


def _build_matchup_table(relevant, profiles, matchups, own_is_home, use_spieltyp=False):
    return {
        (a, b): _matchup_probability(a, b, profiles, matchups, own_is_home=own_is_home, use_spieltyp=use_spieltyp)
        for a in relevant for b in relevant if a != b
    }


def _evaluate_lineups(own, scenarios, profiles, matchups, names, own_is_home, started, use_spieltyp=False, own_double_pairs=None, stronger_double_pair=1, doubles_stats=None, own_on_letters=None, fixed_order=None, fixed_doubles_on=None, fixed_game_pairs=None):
    schedule = _schedule_for_orientation(own_is_home if own_on_letters is None else own_on_letters)
    pair_a, pair_b = _normalize_own_double_pairs(own, own_double_pairs, profiles)
    doubles_stats = doubles_stats or {}
    relevant = set(own)
    for _, order in scenarios:
        relevant.update(order)
    matchup_p = _build_matchup_table(relevant, profiles, matchups, own_is_home, use_spieltyp=use_spieltyp)
    scenario_cache = _build_scenario_doubles_cache(
        scenarios, pair_a, pair_b, profiles, doubles_stats, stronger_double_pair, fixed_game_pairs,
    )
    evaluated = []
    orders = [tuple(fixed_order)] if fixed_order is not None else permutations(own)
    for own_order in orders:
        placement, agg, win5, win10, agg5, agg10 = _evaluate_lineup_for_perm(
            own_order, scenario_cache, matchup_p, schedule,
        )
        if fixed_doubles_on in (5, 10) and placement != fixed_doubles_on:
            placement = fixed_doubles_on
            agg = agg5 if placement == 5 else agg10
        evaluated.append({
            'own_player_ids': list(own_order),
            'players': [names.get(pid, f'Spieler {pid}') for pid in own_order],
            'team_win_probability': round(agg['win'], 6),
            'team_draw_probability': round(agg['draw'], 6),
            'team_loss_probability': round(agg['loss'], 6),
            'expected_own_wins': round(agg['expected_own_wins'], 3),
            'expected_opponent_wins': round(agg['expected_opponent_wins'], 3),
            'expected_score_display': _format_match_score_display(agg['win'], agg['expected_own_wins'], agg['expected_opponent_wins']),
            'recommended_doubles_on': placement,
            'doubles_win_probability_on_5': round(win5, 6),
            'doubles_win_probability_on_10': round(win10, 6),
        })
        if time.monotonic() - started > MAX_ANALYSIS_SECONDS:
            raise RuntimeError('Analysis exceeded the internal 5-second safety budget')
    return evaluated, matchup_p


def _cached_opponent_doubles(opp_order, doubles_stats, profiles, names, cache):
    key = tuple(opp_order)
    if key not in cache:
        cache[key] = _opponent_doubles_for_lineup(opp_order, doubles_stats, profiles, names)
    return cache[key]


def _load_doubles_stats(db, own, opp_ids, own_team, opponent_team, ref_date):
    doubles_stats = {}
    if own_team:
        doubles_stats.update(load_doubles_pair_stats(db, own, team=own_team, ref_date=ref_date))
    if opponent_team and opp_ids:
        doubles_stats.update(load_doubles_pair_stats(db, list(opp_ids), team=opponent_team, ref_date=ref_date))
    if not doubles_stats:
        doubles_stats.update(load_doubles_pair_stats(db, list(set(own) | set(opp_ids)), team=None, ref_date=ref_date))
    return doubles_stats


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
            'recommended_doubles_on': home_item.get('recommended_doubles_on', 5) if home_item['team_win_probability'] >= away_item['team_win_probability'] else away_item.get('recommended_doubles_on', 5),
            'doubles_win_probability_on_5': round((home_item.get('doubles_win_probability_on_5', 0) + away_item.get('doubles_win_probability_on_5', 0)) / 2.0, 6),
            'doubles_win_probability_on_10': round((home_item.get('doubles_win_probability_on_10', 0) + away_item.get('doubles_win_probability_on_10', 0)) / 2.0, 6),
        })
    return merged


def _explain_recommendation(own_order, scenarios, matchup_p, profiles, names, evaluated, own_is_home=None, own_double_pairs=None, stronger_double_pair=1, doubles_stats=None, recommended_doubles_on=5, own_on_letters=None, fixed_game_pairs=None):
    """Create a human-readable, model-grounded explanation for the top lineup."""
    weighted_games = [0.0] * TOTAL_GAMES
    weighted_double = [0.0, 0.0]
    position_rates = {pid: [0.0] * 4 for pid in own_order}
    pair_a, pair_b = _normalize_own_double_pairs(own_order, own_double_pairs, profiles)

    schedule = _schedule_for_orientation(
        (True if own_is_home is not False else False) if own_on_letters is None else own_on_letters
    )

    for scenario_probability, opp_order in scenarios:
        _, game_probs, doubles = _scenario_match_outcome(
            own_order, opp_order, schedule, matchup_p, pair_a, pair_b, profiles, doubles_stats or {},
            recommended_doubles_on, stronger_double_pair,
            fixed_game_pairs=fixed_game_pairs,
        )
        played = _game_play_probabilities(game_probs)
        for i, p in enumerate(game_probs):
            weighted_games[i] += scenario_probability * p
        for i, p in enumerate(doubles):
            weighted_double[i] += scenario_probability * p

        for pid in own_order:
            for position in range(4):
                rate = sum(
                    matchup_p.get((pid, opp_order[opp_idx]), 0.5) * played[game_index]
                    for single_index, (own_idx, opp_idx) in enumerate(schedule)
                    if own_idx == position
                    for game_index in [SINGLE_GAME_PROBABILITY_INDICES[single_index]]
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

    if weighted_double[0] >= 0.01 or weighted_double[1] >= 0.01:
        bullets.append(
            f"Für die empfohlene Doppel-Platzierung ergeben sich im Modell ca. "
            f"{weighted_double[0] * 100:.1f} % Siegchance im Doppel (Spiel 5) und "
            f"{weighted_double[1] * 100:.1f} % (Spiel 10) — beides fließt in die Mannschafts-Siegchance ein."
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
SINGLE_GAME_PROBABILITY_INDICES = (0, 1, 2, 3, 5, 6, 7, 8, 10, 11, 12, 13)


def _expected_singles_wins_for_lineup(
    own_order, scenarios, matchup_p, own_is_home, own_on_letters=None,
    own_double_pairs=None, profiles=None, doubles_stats=None,
    stronger_double_pair=1, recommended_doubles_on=5,
):
    """Expected singles wins, weighted by the probability each game is played."""
    schedule = _schedule_for_orientation(bool(own_is_home) if own_on_letters is None else own_on_letters)
    expected = {pid: 0.0 for pid in own_order}
    pair_a, pair_b = _normalize_own_double_pairs(own_order, own_double_pairs, profiles or {})
    for scenario_probability, opp_order in scenarios:
        singles = [
            matchup_p.get((own_order[own_idx], opp_order[opp_idx]), 0.5)
            for own_idx, opp_idx in schedule
        ]
        doubles5, doubles10 = _doubles_probs_for_scenario(
            opp_order, pair_a, pair_b, profiles or {}, doubles_stats or {},
            stronger_double_pair,
        )
        doubles = doubles5 if int(recommended_doubles_on) == 5 else doubles10
        played = _game_play_probabilities(_build_game_probs(singles, doubles))
        for single_index, (own_idx, opp_idx) in enumerate(schedule):
            pid = own_order[own_idx]
            game_index = SINGLE_GAME_PROBABILITY_INDICES[single_index]
            expected[pid] += scenario_probability * singles[single_index] * played[game_index]
    return expected


def _expected_singles_breakdown_for_lineup(
    own_order, scenarios, matchup_p, names, own_is_home, own_on_letters=None,
    own_double_pairs=None, profiles=None, doubles_stats=None,
    stronger_double_pair=1, recommended_doubles_on=5,
):
    """Per-player singles matchups with played and conditional win probability."""
    schedule = _schedule_for_orientation(bool(own_is_home) if own_on_letters is None else own_on_letters)
    breakdown = {pid: [] for pid in own_order}
    pair_a, pair_b = _normalize_own_double_pairs(own_order, own_double_pairs, profiles or {})
    for game_idx, (own_idx, opp_idx) in enumerate(schedule):
        pid = own_order[own_idx]
        weighted_prob = 0.0
        opponent_ids = set()
        most_likely_opponent_id = scenarios[0][1][opp_idx] if scenarios else None
        for scenario_probability, opp_order in scenarios:
            opp_id = opp_order[opp_idx]
            opponent_ids.add(opp_id)
            weighted_prob += scenario_probability * matchup_p.get((pid, opp_id), 0.5)
        # The detail view intentionally uses the same most-likely scenario(s)
        # as its matchup probabilities, but applies the actual stop rule.
        play_probability = 0.0
        for scenario_probability, opp_order in scenarios:
            singles = [
                matchup_p.get((own_order[o], opp_order[a]), 0.5)
                for o, a in schedule
            ]
            doubles5, doubles10 = _doubles_probs_for_scenario(
                opp_order, pair_a, pair_b, profiles or {}, doubles_stats or {},
                stronger_double_pair,
            )
            doubles = doubles5 if int(recommended_doubles_on) == 5 else doubles10
            game_probs = _build_game_probs(singles, doubles)
            play_probability += scenario_probability * _game_play_probabilities(game_probs)[
                SINGLE_GAME_PROBABILITY_INDICES[game_idx]
            ]
        single_opponent_id = next(iter(opponent_ids)) if len(opponent_ids) == 1 else None
        breakdown[pid].append({
            'game_number': SINGLE_GAME_NUMBERS[game_idx],
            'opponent_player_id': single_opponent_id,
            'opponent_player_ids': sorted(opponent_ids),
            'opponent_name': names.get(most_likely_opponent_id, f'Spieler {most_likely_opponent_id}') if most_likely_opponent_id else 'verschiedene Gegner',
            'opponent_name_is_most_likely_scenario': len(opponent_ids) > 1,
            'win_probability': round(weighted_prob, 3),
            'played_probability': round(play_probability, 6),
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
    own_on_letters=None,
):
    """Explain exact singles matchups for the most likely opponent lineup."""
    if not breakdown or exp_raw is None or exp_rounded is None:
        return None

    pos_label = ('ABCD' if own_on_letters is not False else '1234')[position]
    game_parts = [
        f"Sp. {m['game_number']} vs {_player_short_name(m['opponent_name'])} "
        f"({'Ø ' if m.get('opponent_name_is_most_likely_scenario') else ''}"
        f"{m['win_probability'] * 100:.0f} %, nur in {m.get('played_probability', 1.0) * 100:.0f} % gespielt)"
        for m in breakdown
    ]
    raw_txt = f"{exp_raw:.2f}".replace('.', ',')
    if abs(exp_raw - exp_rounded) >= 0.01:
        sum_part = f"Summe {raw_txt}, gerundet {exp_rounded} Einzel"
    else:
        sum_part = f"Erwartet {exp_rounded} Einzel"

    text_parts = [f"Gegen die wahrscheinlichste Gegneraufstellung: Position {pos_label} — {', '.join(game_parts)}; {sum_part}."]

    h2h_bits = []
    seen = set()
    for m in breakdown:
        candidate_ids = m.get('opponent_player_ids') or ([m['opponent_player_id']] if m['opponent_player_id'] else [])
        for opp_id in candidate_ids:
            if opp_id in seen:
                continue
            wins, games = matchups.get((pid, opp_id), (0, 0))
            if games:
                own = _player_short_name(names.get(pid, pid))
                opp = _player_short_name(names.get(opp_id, opp_id))
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


def _players_for_ids(player_ids, names):
    return [{'id': pid, 'name': names.get(pid, f'Spieler {pid}')} for pid in player_ids]


def _doubles_game_block(player_ids, names, win_probability=None):
    block = {
        'player_ids': list(player_ids),
        'players': _players_for_ids(player_ids, names),
    }
    if win_probability is not None:
        block['win_probability'] = round(float(win_probability), 6)
    return block


def _opponent_doubles_for_lineup(opp_order, doubles_stats, profiles, names):
    predicted = predict_opponent_doubles_lineup(list(opp_order), doubles_stats or {}, profiles, _combined_strength)
    return {
        'game5': _doubles_game_block(predicted['game5'], names),
        'game10': _doubles_game_block(predicted['game10'], names),
        'pair_strong': _doubles_game_block(predicted['pair_strong'], names),
        'pair_weak': _doubles_game_block(predicted['pair_weak'], names),
        'strong_on_game10_probability': predicted['strong_on_game10_probability'],
    }


def _build_doubles_advice(recommendation, names, own_double_pairs, stronger_double_pair, profiles):
    own_order = recommendation['own_player_ids']
    pair_a, pair_b = _normalize_own_double_pairs(own_order, own_double_pairs, profiles)
    recommended = int(recommendation.get('recommended_doubles_on', 5))
    game5_own, game10_own = _resolve_own_doubles_for_games(pair_a, pair_b, recommended, stronger_double_pair)
    return {
        'pair_a': _players_for_ids(pair_a, names),
        'pair_b': _players_for_ids(pair_b, names),
        'stronger_pair_selected': int(stronger_double_pair),
        'stronger_on_recommended': recommended,
        'team_win_probability_strong_on_5': round(float(recommendation.get('doubles_win_probability_on_5', 0)), 6),
        'team_win_probability_strong_on_10': round(float(recommendation.get('doubles_win_probability_on_10', 0)), 6),
        'game5': _doubles_game_block(game5_own, names),
        'game10': _doubles_game_block(game10_own, names),
    }


def _build_info_summary(
    own, scenarios, profiles, names, matchups, recommendation, evaluated, explanation,
    opponent_team, ref_date, opponent_pool, source, orientation_note, matchup_p=None, own_is_home=None, own_on_letters=None,
    own_double_pairs=None, stronger_double_pair=1, doubles_stats=None,
):
    own_order = recommendation['own_player_ids']
    expected_singles = {}
    singles_breakdown = {}
    detail_scenarios = [(1.0, scenarios[0][1])] if scenarios else []
    if matchup_p is not None and own_is_home is not None:
        expected_singles = _expected_singles_wins_for_lineup(
            own_order, detail_scenarios, matchup_p, own_is_home, own_on_letters,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
        )
        singles_breakdown = _expected_singles_breakdown_for_lineup(
            own_order, detail_scenarios, matchup_p, names, own_is_home, own_on_letters,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
        )
    elif matchup_p is not None and own_is_home is None:
        schedule_orientation = True if own_on_letters is None else own_on_letters
        home = _expected_singles_wins_for_lineup(
            own_order, detail_scenarios, matchup_p, True, schedule_orientation,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
        )
        away_matchup = _build_matchup_table(
            set(own_order) | {pid for _, order in scenarios for pid in order},
            profiles,
            matchups,
            False,
        )
        away = _expected_singles_wins_for_lineup(
            own_order, detail_scenarios, away_matchup, False, schedule_orientation,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
        )
        expected_singles = {pid: (home[pid] + away[pid]) / 2.0 for pid in own_order}
        home_breakdown = _expected_singles_breakdown_for_lineup(
            own_order, detail_scenarios, matchup_p, names, True, schedule_orientation,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
        )
        away_breakdown = _expected_singles_breakdown_for_lineup(
            own_order, detail_scenarios, away_matchup, names, False, schedule_orientation,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
        )
        singles_breakdown = {
            pid: [
                {
                    **home_breakdown[pid][i],
                    'win_probability': round((home_breakdown[pid][i]['win_probability'] + away_breakdown[pid][i]['win_probability']) / 2.0, 3),
                    'played_probability': round((home_breakdown[pid][i]['played_probability'] + away_breakdown[pid][i]['played_probability']) / 2.0, 6),
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
                pid, position, breakdown, profile, matchups, names, exp_raw, exp_rounded, own_on_letters,
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


def analyze_lineup(own_player_ids, opponent_team, actual_opponent_ids=None, opponent_limit=24, own_is_home=None, use_spieltyp=False, own_double_pairs=None, stronger_double_pair=1, own_team=None, opponent_on_letters=None, fixed_own_order=None, fixed_doubles_on=None):
    started = time.monotonic(); own = [str(x) for x in own_player_ids]
    if len(own) != 4 or len(set(own)) != 4:
        raise ValueError('exactly four different own_player_ids are required')
    fixed = None if fixed_own_order is None else [str(x) for x in fixed_own_order]
    if fixed is not None and (len(fixed) != 4 or len(set(fixed)) != 4 or set(fixed) != set(own)):
        raise ValueError('fixed_own_order must contain the same four own_player_ids')
    if fixed_doubles_on is not None and int(fixed_doubles_on) not in (5, 10):
        raise ValueError('fixed_doubles_on must be 5 or 10')
    fixed_game_pairs = None
    if fixed is not None:
        pair_a, pair_b = _normalize_own_double_pairs(own, own_double_pairs, {})
        if own_double_pairs is not None:
            # In a fixed request the client sends the pairs as game 5;game 10.
            fixed_game_pairs = (pair_a, pair_b)
        else:
            fixed_game_pairs = _resolve_own_doubles_for_games(
                pair_a, pair_b, fixed_doubles_on or 5, stronger_double_pair,
            )
    if not opponent_team:
        raise ValueError('opponent_team is required')
    actual = None if actual_opponent_ids is None else [str(x) for x in actual_opponent_ids]
    if actual is not None:
        if len(actual) > 4 or len(set(actual)) != len(actual):
            raise ValueError('up to four different known_opponent_ids are allowed')
        if not actual:
            actual = None

    names, profiles, matchups, scenarios, source, ref_date, opponent_pool = _load_analysis_data(own, opponent_team, actual, use_spieltyp=use_spieltyp)
    if not scenarios:
        phase = 'C' if actual and len(actual) == 4 and opponent_on_letters is not None else ('B' if actual else 'A')
        return {'ok': True, 'phase': phase, 'recommendations': [], 'warnings': [f'Keine passende Gegner-Aufstellung für {opponent_team} gefunden.']}

    opp_ids = set()
    for _, order in scenarios:
        opp_ids.update(order)
    with SessionLocal() as db:
        doubles_stats = _load_doubles_stats(db, own, opp_ids, own_team, opponent_team, ref_date)

    _check_analysis_budget(started)
    own_on_letters = None if opponent_on_letters is None else not bool(opponent_on_letters)
    if own_is_home is None:
        home_eval, home_matchups = _evaluate_lineups(own, scenarios, profiles, matchups, names, True, started, use_spieltyp=use_spieltyp, own_double_pairs=own_double_pairs, stronger_double_pair=stronger_double_pair, doubles_stats=doubles_stats, own_on_letters=own_on_letters, fixed_order=fixed, fixed_doubles_on=fixed_doubles_on, fixed_game_pairs=fixed_game_pairs)
        away_eval, _ = _evaluate_lineups(own, scenarios, profiles, matchups, names, False, started, use_spieltyp=use_spieltyp, own_double_pairs=own_double_pairs, stronger_double_pair=stronger_double_pair, doubles_stats=doubles_stats, own_on_letters=own_on_letters, fixed_order=fixed, fixed_doubles_on=fixed_doubles_on, fixed_game_pairs=fixed_game_pairs)
        evaluated = _merge_orientations(home_eval, away_eval)
        matchup_p = home_matchups
        orientation_note = 'home-and-away-averaged' if own_on_letters is None else ('opponent-A-D' if opponent_on_letters else 'opponent-1-4')
    else:
        evaluated, matchup_p = _evaluate_lineups(own, scenarios, profiles, matchups, names, bool(own_is_home), started, use_spieltyp=use_spieltyp, own_double_pairs=own_double_pairs, stronger_double_pair=stronger_double_pair, doubles_stats=doubles_stats, own_on_letters=own_on_letters, fixed_order=fixed, fixed_doubles_on=fixed_doubles_on, fixed_game_pairs=fixed_game_pairs)
        orientation_note = ('home' if own_is_home else 'away') if opponent_on_letters is None else ('opponent-A-D' if opponent_on_letters else 'opponent-1-4')

    if fixed is None:
        # The normal route evaluates and ranks all 24 permutations.
        evaluated.sort(key=lambda x: (-x['team_win_probability'], x['own_player_ids']))
    else:
        # Evaluate the optimum separately for a direct, apples-to-apples comparison.
        if own_is_home is None:
            optimal_home, _ = _evaluate_lineups(own, scenarios, profiles, matchups, names, True, started, use_spieltyp=use_spieltyp, own_double_pairs=own_double_pairs, stronger_double_pair=stronger_double_pair, doubles_stats=doubles_stats, own_on_letters=own_on_letters)
            optimal_away, _ = _evaluate_lineups(own, scenarios, profiles, matchups, names, False, started, use_spieltyp=use_spieltyp, own_double_pairs=own_double_pairs, stronger_double_pair=stronger_double_pair, doubles_stats=doubles_stats, own_on_letters=own_on_letters)
            optimal_eval = _merge_orientations(optimal_home, optimal_away)
        else:
            optimal_eval, _ = _evaluate_lineups(own, scenarios, profiles, matchups, names, bool(own_is_home), started, use_spieltyp=use_spieltyp, own_double_pairs=own_double_pairs, stronger_double_pair=stronger_double_pair, doubles_stats=doubles_stats, own_on_letters=own_on_letters)
        optimal_eval.sort(key=lambda x: (-x['team_win_probability'], x['own_player_ids']))
        optimal_recommendation = optimal_eval[0]
    if fixed is not None:
        recommendation = evaluated[0]
    else:
        recommendation = evaluated[0]
    for rank, item in enumerate(evaluated, 1):
        item['rank'] = rank
    doubles_advice = _build_doubles_advice(
        recommendation, names, own_double_pairs, stronger_double_pair, profiles,
    )
    recommendation['doubles'] = {
        'game5': doubles_advice['game5'],
        'game10': doubles_advice['game10'],
    }
    explanation = _explain_recommendation(
        recommendation['own_player_ids'], scenarios, matchup_p, profiles, names, evaluated, own_is_home,
        own_double_pairs=own_double_pairs, stronger_double_pair=stronger_double_pair, doubles_stats=doubles_stats,
        recommended_doubles_on=recommendation.get('recommended_doubles_on', 5),
        own_on_letters=own_on_letters,
        fixed_game_pairs=fixed_game_pairs,
    )
    opponent_doubles_by_order = {}
    opponent_predictions = [
        {
            'player_ids': list(order),
            'players': _players_for_ids(order, names),
            'probability': round(probability, 6),
            'doubles': _cached_opponent_doubles(order, doubles_stats, profiles, names, opponent_doubles_by_order),
        }
        for probability, order in scenarios
    ]
    opponent_predictions.sort(key=lambda item: (-item['probability'], item['player_ids']))
    elapsed = time.monotonic() - started
    info_summary = _build_info_summary(
        own, scenarios, profiles, names, matchups, recommendation, evaluated, explanation,
        opponent_team, ref_date, opponent_pool, source, orientation_note,
        matchup_p=matchup_p, own_is_home=own_is_home, own_on_letters=own_on_letters,
        own_double_pairs=own_double_pairs, stronger_double_pair=stronger_double_pair,
        doubles_stats=doubles_stats,
    )

    return {
        'ok': True,
        'phase': 'C' if actual and len(actual) == 4 and opponent_on_letters is not None else ('B' if actual else 'A'),
        'known_opponent_ids': actual or [],
        'known_opponent_count': len(actual or []),
        'own_is_home': own_is_home,
        'opponent_direction_known': opponent_on_letters is not None,
        'opponent_on_letters': opponent_on_letters,
        'use_spieltyp': use_spieltyp,
        'opponent_team': opponent_team,
        'own_player_ids': own,
        'opponent_set_source': source,
        'recommendation': recommendation,
        'recommendations': evaluated,
        **({'optimal_recommendation': optimal_recommendation} if fixed is not None else {}),
        'explanation': explanation,
        'doubles_advice': doubles_advice,
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
            'doubles_pairing': 'own pairs user-defined with user-selected stronger pair; opponent pairs from doubles history',
            'strength_formula': (
                f'RC ({RC_COMPONENT_WEIGHT:.2f} * (RC - {RC_BASELINE:.0f}) / {RC_SCALE:.0f}) + '
                f'singles record ({SINGLES_RECORD_WEIGHT:.2f} * smoothed win-rate delta) + '
                f'RC trend (max ±{TREND_MAX_COMPONENT:.2f}) + venue adjustment (max ±{HOME_AWAY_MAX_COMPONENT:.2f})'
            ),
            'rc_baseline': (
                f'{RC_BASELINE:.0f} fixed neutral midpoint: snapshots have no league association, '
                'so no leakage-safe league baseline can be derived from the available schema'
            ),
            'h2h_weight': f'up to {int(H2H_MAX_WEIGHT * 100)}% when direct singles exist',
            'home_away': (
                f'overall record is the base; smoothed home/away delta is separate and capped at ±{HOME_AWAY_MAX_COMPONENT:.2f} '
                f'after {HOME_AWAY_MIN_GAMES} venue games ({HOME_AWAY_MIN_OVERALL_GAMES} overall games required)'
            ),
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
