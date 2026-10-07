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
MAX_ANALYSIS_SECONDS = 7.0
STATS_YEARS = 2
OPPONENT_POOL_YEARS = 2
# Recency decay by completed league rounds (matches), not calendar days.
LINEUP_RECENCY_HALF_LIFE_MATCHES = 8.0
# Legacy alias kept for external scripts that still reference the old name.
LINEUP_RECENCY_HALF_LIFE_DAYS = LINEUP_RECENCY_HALF_LIFE_MATCHES
# Concentrate opponent scenario mass for own-lineup ranking, display, and alternatives.
SCENARIO_SHARPENING_ALPHA = 2.5
# Pseudo-observations blended with joint quartet history; n=5 -> 5/6 joint weight (~83%).
KNOWN_QUARTET_JOINT_PRIOR_STRENGTH = 1.0
POSITION_PRIOR_SMOOTHING = 0.35
# Global strength-lineup prior: strongest RC on A/1, weakest on D/4 (measured across all matches).
GLOBAL_STRENGTH_MIN_SAMPLE = 100
GLOBAL_STRENGTH_MIN_TOP_RATE = 0.52
GLOBAL_STRENGTH_MIN_BOTTOM_RATE = 0.50
STRENGTH_RC_SCALE = 500.0
STRENGTH_POSITION_WEIGHTS = (4.0, 3.0, 2.0, 1.0)  # legacy reference; use _adaptive_strength_position_weights
STRENGTH_RC_SPREAD_FLAT_THRESHOLD = 40.0
STRENGTH_POSITION_WEIGHT_MIN = 1.0
STRENGTH_POSITION_WEIGHT_MAX = 4.0
STRENGTH_POSITION_WEIGHTS_TIGHT = (3.0, 2.66, 2.33, 2.0)  # A/B/C/D when RC spread < threshold
COHESIVE_QUARTET_MIN_MATCHES = 5
COHESIVE_TRIO_MIN_MATCHES = 6
COHESIVE_QUARTET_MIN_RECENCY_MASS = 3.0
COHESIVE_TRIO_MIN_RECENCY_MASS = 3.5
MEDIUM_GROUP_MIN_MATCHES = 4
_global_strength_support_cache: dict[str, tuple[float, float, float, int]] = {}
_lineup_cohesion_cache: dict[tuple[str, str], tuple[int, float, int, float]] = {}
_match_rounds_ago_cache: dict[tuple[str, str], dict[int, int]] = {}


def clear_analysis_runtime_caches() -> None:
    """Drop in-process caches after DB imports."""
    _lineup_cohesion_cache.clear()
    _global_strength_support_cache.clear()
    _match_rounds_ago_cache.clear()
DEFAULT_RC_RATING = 1200.0
TREND_MIN_RC = -100.0
TREND_MAX_RC = 100.0
TREND_WINDOW_DAYS = 365.25
TREND_LEVEL_WEIGHT_FLOOR = 0.35
TREND_INTERMEDIATE_MOVE_CAP = 2.0
TREND_LEVEL_WEIGHT_DECAY = 0.65
TREND_RECENT_SNAPSHOT_MULTIPLIER = 1.25
TREND_RC_HALF_LIFE_DAYS = 90.0
TREND_MAX_COMPONENT = 0.08
TREND_MAX_SINGLES = 25
TREND_MIN_SINGLES = 5
TREND_RECENT_SINGLES = 10
TREND_MAX_SNAPSHOTS = 10
SPIELTYP_MAX_COMPONENT = 0.12
SPIELTYP_MIN_GAMES = 2
TREND_COMPONENT_FULL_SCALE = 80.0
# RC is the primary current-strength signal.  The singles record is only a
# deliberately small corroborating signal; trend gets a comparable bounded
# contribution so recent form can matter without dominating the model.
RC_BASELINE = 1400.0
RC_SCALE = 300.0
RC_COMPONENT_WEIGHT = 0.75
SINGLES_RECORD_WEIGHT = 0.18
HOME_AWAY_MAX_COMPONENT = 0.08
HOME_AWAY_MIN_GAMES = 8
HOME_AWAY_MIN_OVERALL_GAMES = 12
HOME_AWAY_COMPONENT_SCALE = 0.32
H2H_SHRINKAGE_MATCHES = 4.0
H2H_LEGACY_PRIOR_WINS = 1.5
H2H_LEGACY_PRIOR_GAMES = 3.0
H2H_LEGACY_WEIGHT_CAP = 0.85
H2H_LEGACY_WEIGHT_BASE = 0.35
H2H_LEGACY_WEIGHT_GAMES_DIV = 6.0
# 0 = pure shrinkage toward RC base, 1 = legacy weighted direct record, 0.5 = midpoint.
H2H_SHRINKAGE_BLEND = 0.5
MODEL_VERSION = 'rc-h2h-homeaway-v36-sharpen-blend'

# Safe XTTV date parsing — invalid or missing dates become NULL instead of aborting SQL.
_SQL_MATCH_DAY = (
    "CASE WHEN m.match_date ~ '^\\d{2}\\.\\d{2}\\.\\d{4}' "
    "THEN to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') "
    "ELSE NULL END"
)
_SQL_MATCH_DAY_COL = (
    "CASE WHEN match_date ~ '^\\d{2}\\.\\d{2}\\.\\d{4}' "
    "THEN to_date(substring(match_date from 1 for 10), 'DD.MM.YYYY') "
    "ELSE NULL END"
)
_MATCH_DAY_FILTER = f"{_SQL_MATCH_DAY} IS NOT NULL AND {_SQL_MATCH_DAY} >= :cutoff"

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
    try:
        row = db.execute(text(f"""
            SELECT max({_SQL_MATCH_DAY_COL}) AS latest
            FROM xttv_matches
            WHERE match_date IS NOT NULL
              AND match_date ~ '^\\d{{2}}\\.\\d{{2}}\\.\\d{{4}}'
        """)).mappings().first()
        latest = row['latest'] if row else None
        return latest or date.today()
    except Exception:
        return date.today()


def _clamp_probability(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(1.0, number))


def _extend_opponent_pool(pool, *player_groups):
    extended = {str(x) for x in pool}
    for group in player_groups:
        if group:
            extended.update(str(x) for x in group)
    return extended


def _uniform_lineup_scenarios(player_ids):
    players = [str(x) for x in player_ids]
    if len(players) != 4 or len(set(players)) != 4:
        return []
    uniform = 1.0 / 24.0
    return [(uniform, tuple(order)) for order in permutations(players)]


def _team_average_rc(rc_by_player, player_ids, exclude_pid=None):
    """Average RC of teammates with known ratings; DEFAULT_RC_RATING if none."""
    exclude = {str(exclude_pid)} if exclude_pid is not None else set()
    values = []
    for pid in player_ids:
        pid = str(pid)
        if pid in exclude:
            continue
        raw = rc_by_player.get(pid)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(val):
            values.append(val)
    if not values:
        return DEFAULT_RC_RATING
    return sum(values) / len(values)


def _resolve_rc_rating(pid, rc_by_player, player_ids):
    """Use player RC when known; otherwise the average RC of the remaining group."""
    pid = str(pid)
    raw = rc_by_player.get(pid)
    if raw is not None:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            val = None
        else:
            if math.isfinite(val):
                return val
    return _team_average_rc(rc_by_player, player_ids, exclude_pid=pid)


def _effective_rc(rating, deviation=None):
    """Shrink observed RC toward baseline when deviation is large."""
    if rating is None:
        return None
    try:
        value = float(rating)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if deviation is None:
        return value
    try:
        dev = float(deviation)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(dev) or dev <= 0:
        return value
    confidence = 1.0 / (1.0 + dev / RC_SCALE)
    return RC_BASELINE + confidence * (value - RC_BASELINE)


def _impute_missing_rc_ratings(profiles, player_ids):
    """Fill missing RC in profiles using the same teammate-average rule as lineup priors."""
    player_ids = [str(x) for x in player_ids]
    rc_map = {
        pid: profiles.get(pid, _empty_profile()).get('rc_rating')
        for pid in player_ids
    }
    for pid in player_ids:
        profile = profiles.setdefault(pid, _empty_profile())
        if profile.get('rc_rating') is None:
            profile['rc_rating'] = _resolve_rc_rating(pid, rc_map, player_ids)
            profile['rc_imputed'] = True
        else:
            profile['rc_imputed'] = False


def _pick_quartet_from_pool(db, pool, ref_date=None, prefer=None):
    """Pick four distinct opponent ids, preferring known players then RC strength."""
    del ref_date
    prefer = [str(x) for x in (prefer or [])]
    pool = {str(x) for x in pool}
    chosen = []
    for pid in prefer:
        if pid not in chosen:
            chosen.append(pid)
    if len(chosen) > 4:
        chosen = chosen[:4]
    if len(chosen) == 4:
        return chosen
    remaining = 4 - len(chosen)
    candidates = sorted(pool - set(chosen))
    if len(candidates) < remaining:
        return None
    if not candidates:
        return chosen if len(chosen) == 4 else None
    rc_map = _load_latest_rc_map(db, candidates)
    team_context = chosen + candidates
    ranked = sorted(
        candidates,
        key=lambda pid: _resolve_rc_rating(pid, rc_map, team_context),
        reverse=True,
    )
    return chosen + ranked[:remaining]


def _scenarios_from_strength_prior_lineup(db, player_ids):
    players = [str(x) for x in player_ids]
    if len(players) != 4 or len(set(players)) != 4:
        return []
    rc_map = _load_latest_rc_map(db, players)
    return _scenarios_from_strength_prior(players, rc_map)


def _build_fallback_opponent_scenarios(db, opponent_pool, ref_date, actual=None):
    """Last-resort scenarios when history is missing or was filtered away."""
    warnings = []
    prefer = [str(x) for x in (actual or [])]
    quartet = None
    if actual and len(actual) == 4 and len(set(actual)) == 4:
        quartet = [str(x) for x in actual]
    else:
        quartet = _pick_quartet_from_pool(db, opponent_pool, ref_date, prefer=prefer)
    if not quartet:
        pool_size = len(opponent_pool)
        if pool_size < 4:
            warnings.append(
                f'Nur {pool_size} Spieler im Gegner-Kader gefunden (mindestens 4 nötig). '
                'Prüfe Teamname und importierte XTTV-Daten.',
            )
        else:
            warnings.append('Kein vollständiges Gegner-Quartett aus dem Kader ableitbar.')
        return [], 'fallback-unavailable', warnings

    strength_scenarios = _scenarios_from_strength_prior_lineup(db, quartet)
    if strength_scenarios:
        warnings.append(
            'Keine verwertbare Aufstellungshistorie — Schätzung basiert auf RC-Stärke-Prior.',
        )
        return strength_scenarios, 'strength-prior-fallback', warnings

    uniform = _uniform_lineup_scenarios(quartet)
    warnings.append(
        'Keine Historie und keine RC-Daten — alle 24 Aufstellungen gleich wahrscheinlich.',
    )
    return uniform, 'all-24-uniform-fallback', warnings


def _cutoff(ref, years):
    return ref - timedelta(days=int(round(years * 365.25)))


def _lineup_recency_weight_by_rounds(rounds_ago: int | float) -> float:
    """Exponential decay by league rounds completed before the reference match."""
    age = max(0.0, float(rounds_ago))
    return math.pow(0.5, age / LINEUP_RECENCY_HALF_LIFE_MATCHES)


def _build_match_rounds_ago(db, ref_date) -> dict[int, int]:
    """Map match_id -> rounds before ref_date (0 = most recent completed round)."""
    ref_date = ref_date or _reference_date(db)
    cache_key = (str(ref_date), str(_cutoff(ref_date, STATS_YEARS)))
    cached = _match_rounds_ago_cache.get(cache_key)
    if cached is not None:
        return cached
    rows = db.execute(
        text(f"""
        SELECT m.id AS match_id
        FROM xttv_matches m
        WHERE {_SQL_MATCH_DAY} IS NOT NULL
          AND {_SQL_MATCH_DAY} <= :ref_date
          AND {_MATCH_DAY_FILTER}
        ORDER BY {_SQL_MATCH_DAY} DESC, m.id DESC
        """),
        {'ref_date': ref_date, 'cutoff': _cutoff(ref_date, STATS_YEARS)},
    ).mappings()
    result = {int(row['match_id']): idx for idx, row in enumerate(rows)}
    _match_rounds_ago_cache[cache_key] = result
    return result


def _build_team_match_rounds_ago(db, team: str, ref_date) -> dict[int, int]:
    """Team-scoped rounds map — avoids scanning every league match on each analysis."""
    ref_date = ref_date or _reference_date(db)
    cache_key = ('team', team, str(ref_date), str(_cutoff(ref_date, STATS_YEARS)))
    cached = _match_rounds_ago_cache.get(cache_key)
    if cached is not None:
        return cached
    rows = db.execute(
        text(f"""
        SELECT m.id AS match_id
        FROM xttv_matches m
        WHERE (m.home_team = :team OR m.away_team = :team)
          AND {_SQL_MATCH_DAY} IS NOT NULL
          AND {_SQL_MATCH_DAY} <= :ref_date
          AND {_MATCH_DAY_FILTER}
        ORDER BY {_SQL_MATCH_DAY} DESC, m.id DESC
        """),
        {'team': team, 'ref_date': ref_date, 'cutoff': _cutoff(ref_date, STATS_YEARS)},
    ).mappings()
    result = {int(row['match_id']): idx for idx, row in enumerate(rows)}
    _match_rounds_ago_cache[cache_key] = result
    return result


def _lineup_recency_weight(
    match_date,
    ref_date,
    *,
    match_id=None,
    rounds_ago=None,
    rounds_map=None,
):
    """Recency weight for lineup observations (round-based, not calendar-based)."""
    del match_date
    if rounds_ago is not None:
        return _lineup_recency_weight_by_rounds(rounds_ago)
    if match_id is not None and rounds_map is not None:
        idx = rounds_map.get(int(match_id))
        if idx is not None:
            return _lineup_recency_weight_by_rounds(idx)
    # Unknown round index: moderate stale weight (avoid calendar-day fallback).
    return _lineup_recency_weight_by_rounds(4.0)


def _empty_profile():
    return {
        'wins': 0, 'games': 0,
        'home_wins': 0, 'home_games': 0,
        'away_wins': 0, 'away_games': 0,
        'rc_rating': None, 'rc_deviation': None, 'rc_imputed': False,
        'rc_trend': None, 'rc_trend_momentum': None, 'trend_component': 0.0,
        'spieltyp': None, 'style_matchups': {}, 'style_component': 0.0,
    }


def _valid_rc_snapshots(snapshots):
    """Return RC snapshots with usable ratings, ordered by observation time."""
    return sorted(
        (snapshot for snapshot in snapshots if snapshot.get('rc_rating') is not None),
        key=lambda snapshot: snapshot['observed_at'],
    )


def _weighted_rc_momentum(snapshots, recent_boundary=None):
    """Return a robust trend from only the latest ten RC observations.

    The median of point changes makes the signal resistant to one exceptional
    event. The newest-ten-singles segment receives a modest boost.
    ``recent_boundary`` is a date/datetime and is inclusive because all games
    on the boundary day belong to that segment.
    """
    ordered = _valid_rc_snapshots(snapshots)
    if len(ordered) < 2:
        return 0.0
    opening = ordered[0]
    current = ordered[-1]

    def day(value):
        return value.date() if hasattr(value, 'date') else value

    recent = ordered[-min(10, len(ordered)):]
    changes = [
        float(right['rc_rating']) - float(left['rc_rating'])
        for left, right in zip(recent, recent[1:])
    ]
    if not changes:
        trend = 0.0
    elif len(recent) <= 3:
        # With only a very short series there is not enough evidence for a
        # robust median; retain the direct net signal.
        recent_segment_weight = (
            TREND_RECENT_SNAPSHOT_MULTIPLIER
            if recent_boundary is not None
            and day(current['observed_at']) >= day(recent_boundary)
            else 1.0
        )
        trend = 0.5 * (
            (float(current['rc_rating']) - float(recent[0]['rc_rating']))
            * recent_segment_weight
        )
    else:
        # Use the latest ten snapshots. A single unusually large change is
        # treated as an outlier; repeated large changes remain fully relevant.
        median_abs = sorted(abs(change) for change in changes)[len(changes) // 2]
        extreme = [
            change for change in changes
            # A large positive RC jump is evidence of an improving player,
            # not an outlier to suppress. Only isolated negative shocks are
            # eligible for removal from an otherwise stable trajectory.
            if change < -max(30.0, 2.5 * median_abs)
        ]
        adjusted = [
            (left, right, change)
            for left, right, change in zip(recent, recent[1:], changes)
            if not (len(extreme) == 1 and change == extreme[0])
        ]
        isolated_outlier_removed = len(extreme) == 1 and len(adjusted) < len(changes)
        weighted_changes = []
        current_day = day(current['observed_at'])
        for left, right, change in adjusted:
            if change == 0:
                # A repeated RC snapshot is not evidence of momentum and
                # must not dilute the last real rating change.
                continue
            age_days = max(0, (current_day - day(right['observed_at'])).days)
            weight = math.pow(0.5, age_days / TREND_RC_HALF_LIFE_DAYS)
            if recent_boundary is not None and day(right['observed_at']) >= day(recent_boundary):
                weight *= TREND_RECENT_SNAPSHOT_MULTIPLIER
            weighted_changes.append((change, weight))
        if not weighted_changes:
            trend = 0.0
        else:
            weighted_mean = sum(change * weight for change, weight in weighted_changes) / sum(
                weight for _, weight in weighted_changes
            )
            raw_change = sum(change for change, _ in weighted_changes)
            positive_count = sum(change > 0 for change, _ in weighted_changes)
            negative_count = sum(change < 0 for change, _ in weighted_changes)
            central = sorted(change for change, _ in weighted_changes)[len(weighted_changes) // 2]
            if abs(raw_change) >= 30:
                # Sustained movement uses cumulative change; recency remains
                # a secondary correction.
                trend = 1.5 * (
                    0.7 * raw_change + 0.3 * weighted_mean * len(weighted_changes)
                )
            else:
                trend = 1.5 * central
            if trend < 0 and negative_count < 3:
                trend = 0.0
            elif trend < 0:
                # Sustained deterioration should remain visible on the
                # -100..+100 scale.
                trend *= 2.0
            elif raw_change < 20 and negative_count >= 2:
                # A small net rise with several setbacks is effectively
                # neutral rather than a strong upward trend.
                trend *= 0.25
            elif abs(central) <= 5:
                trend = 1.5 * central
            elif positive_count >= 3 and negative_count >= 3:
                # Mixed trajectories are not strong upward trends. Use the
                # robust central change instead of summing every fluctuation.
                trend = 1.5 * central
            elif raw_change <= 0:
                # A recovery after a net-flat/negative period is only a
                # small positive signal, not a strong upward trend.
                trend *= 0.25
            if isolated_outlier_removed and extreme[0] < 0 and trend < 0:
                # A single isolated loss is insufficient evidence for a
                # negative trend when the surrounding trajectory is stable.
                trend = 0.0
        # Keep the display scale at -100..+100 without making ordinary
        # multi-match swings hit the limits. Very exceptional movement can
        # still approach the ends of the scale.
        trend = 100.0 * math.tanh(trend / 240.0)
        if trend == 0.0:
            # Preserve a small directional signal instead of displaying a
            # mathematically exact zero after the even-number UI rounding.
            nonzero_changes = [change for change in changes if change]
            if nonzero_changes:
                trend = 2.0 if nonzero_changes[-1] > 0 else -2.0
    return max(TREND_MIN_RC, min(TREND_MAX_RC, trend))


def _recent_rc_delta(snapshots, max_steps: int = 3) -> float:
    """RC rating change across the last N RC observations."""
    ordered = _valid_rc_snapshots(snapshots)
    if len(ordered) < 2:
        return 0.0
    window = min(len(ordered), max_steps + 1)
    subset = ordered[-window:]
    return float(subset[-1]['rc_rating']) - float(subset[0]['rc_rating'])


def _net_rc_change(snapshots):
    """Net RC change between the first and last valid observation."""
    ordered = _valid_rc_snapshots(snapshots)
    if len(ordered) < 2:
        return None
    return float(ordered[-1]['rc_rating']) - float(ordered[0]['rc_rating'])


def _recent_singles_all_3_0(recent_singles: list[dict]) -> bool:
    if len(recent_singles) < 3:
        return False
    for row in recent_singles[:3]:
        if int(row['own_score']) != 3 or int(row['opp_score']) != 0:
            return False
    return True


def _latest_rc_snapshots_for_trend(
    snapshots: list[dict],
    *,
    limit: int = TREND_MAX_SNAPSHOTS,
) -> list[dict]:
    """Latest RC observations used for trend (typically one snapshot per league round)."""
    valid = _valid_rc_snapshots(snapshots)
    return valid[-limit:]


def _trend_snapshot_window(snapshots: list[dict], recent_singles: list[dict] | None = None) -> list[dict]:
    """RC snapshots in the trend window (last ``TREND_MAX_SNAPSHOTS`` observations)."""
    del recent_singles
    return _latest_rc_snapshots_for_trend(snapshots)


def _recent_segment_boundary(recent_singles: list[dict]):
    """Return the inclusive date boundary of the newest ten singles."""
    ordered = _recent_singles_window(recent_singles)
    if not ordered:
        return None
    ordered = sorted(ordered, key=lambda row: row['match_day'], reverse=True)
    return ordered[min(TREND_RECENT_SINGLES, len(ordered)) - 1]['match_day']


def _snapshot_level_at_or_before(snapshots, boundary):
    """Return the latest valid RC level at or before a segment boundary."""
    def day(value):
        return value.date() if hasattr(value, 'date') else value

    candidates = [
        snapshot for snapshot in _valid_rc_snapshots(snapshots)
        if day(snapshot['observed_at']) <= day(boundary)
    ]
    return candidates[-1] if candidates else None


def _recent_singles_window(recent_singles: list[dict]) -> list[dict]:
    """Return the latest 25 singles, retaining all games on the boundary day."""
    ordered = sorted(recent_singles, key=lambda row: row['match_day'], reverse=True)
    if len(ordered) <= TREND_MAX_SINGLES:
        return ordered
    boundary = ordered[TREND_MAX_SINGLES - 1]['match_day']
    return [row for row in ordered if row['match_day'] >= boundary]


def _compute_trend_metrics(
    snapshots_1y: list[dict],
    recent_singles: list[dict],
    *,
    include_latest_snapshot: bool = False,
) -> tuple[float | None, float]:
    """Return (bounded RC trend for display, model component).

    Statistics and lineup use the same rule: the latest up to
    ``TREND_MAX_SNAPSHOTS`` RC observations (typically one per league round
    after Ratings Central updates). At least two snapshots are required.
    XTTV singles are not used to define this window.
    """
    del recent_singles, include_latest_snapshot
    window = _latest_rc_snapshots_for_trend(snapshots_1y)
    if len(window) < 2:
        return None, 0.0
    momentum = _weighted_rc_momentum(window)
    momentum = max(TREND_MIN_RC, min(TREND_MAX_RC, momentum))
    component = max(
        -TREND_MAX_COMPONENT,
        min(TREND_MAX_COMPONENT, momentum / TREND_COMPONENT_FULL_SCALE * TREND_MAX_COMPONENT),
    )
    return momentum, component


def _rc_trend_from_snapshots(snapshots):
    """Recency-weighted RC momentum for an already selected snapshot window."""
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


def _h2h_shrunk_probability(wins, games, base):
    return (wins + H2H_SHRINKAGE_MATCHES * base) / (games + H2H_SHRINKAGE_MATCHES)


def _h2h_legacy_probability(wins, games, base):
    direct = (wins + H2H_LEGACY_PRIOR_WINS) / (games + H2H_LEGACY_PRIOR_GAMES)
    weight = min(
        H2H_LEGACY_WEIGHT_CAP,
        H2H_LEGACY_WEIGHT_BASE + games / H2H_LEGACY_WEIGHT_GAMES_DIV,
    )
    return (1.0 - weight) * base + weight * direct


def _h2h_blended_probability(wins, games, base):
    shrunk = _h2h_shrunk_probability(wins, games, base)
    legacy = _h2h_legacy_probability(wins, games, base)
    return (1.0 - H2H_SHRINKAGE_BLEND) * shrunk + H2H_SHRINKAGE_BLEND * legacy


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
    return _clamp_probability(_h2h_blended_probability(wins, games, base))


def _lookup_matchup_probability(
    matchup_p,
    own_id,
    opp_id,
    profiles,
    matchups,
    own_is_home=True,
    use_spieltyp=False,
):
    """Resolve a singles probability without silently defaulting to 0.5."""
    key = (own_id, opp_id)
    if key in matchup_p:
        return matchup_p[key]
    return _matchup_probability(
        own_id, opp_id, profiles, matchups,
        own_is_home=own_is_home, use_spieltyp=use_spieltyp,
    )


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
    fixed_game_pairs=None, matchups=None, own_is_home=True, use_spieltyp=False,
):
    matchups = matchups or {}
    singles = [
        _lookup_matchup_probability(
            matchup_p, own_order[own_idx], opp_order[opp_idx],
            profiles, matchups, own_is_home=own_is_home, use_spieltyp=use_spieltyp,
        )
        for own_idx, opp_idx in schedule
    ]
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


def _evaluate_lineup_for_perm(
    own_order, scenario_cache, matchup_p, schedule, *,
    profiles, matchups, own_is_home=True, use_spieltyp=False,
):
    agg5 = _empty_match_agg()
    agg10 = _empty_match_agg()
    for scenario_probability, opp_order, doubles5, doubles10 in scenario_cache:
        singles = [
            _lookup_matchup_probability(
                matchup_p, own_order[own_idx], opp_order[opp_idx],
                profiles, matchups, own_is_home=own_is_home, use_spieltyp=use_spieltyp,
            )
            for own_idx, opp_idx in schedule
        ]
        for agg, doubles in ((agg5, doubles5), (agg10, doubles10)):
            game_probs = _build_game_probs(singles, doubles)
            _accumulate_match_agg(agg, _team_result_distribution(game_probs), scenario_probability)
    win5 = agg5['win']
    win10 = agg10['win']
    recommended = 5 if win5 >= win10 else 10
    return recommended, (agg5 if recommended == 5 else agg10), win5, win10, agg5, agg10


def _check_analysis_budget(started):
    if time.monotonic() - started > MAX_ANALYSIS_SECONDS:
        raise RuntimeError('Analysis exceeded the internal 7-second safety budget')


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


def _trivial_strength_own_order(own, profiles):
    """Strongest combined strength on A, weakest on D (trivial RC lineup)."""
    return sorted(
        [str(x) for x in own],
        key=lambda pid: -_combined_strength(profiles.get(pid, _empty_profile())),
    )


def _all_double_pair_partitions(player_ids):
    """All ways to split four players into two doubles pairs."""
    players = sorted([str(x) for x in player_ids])
    partitions = []
    seen = set()
    for pair_a in combinations(players, 2):
        rest = tuple(p for p in players if p not in pair_a)
        if len(rest) != 2:
            continue
        key = frozenset([frozenset(pair_a), frozenset(rest)])
        if key in seen:
            continue
        seen.add(key)
        partitions.append((tuple(pair_a), rest))
    return partitions


def _compute_lineup_configuration_spread_pp(
    own, scenarios, profiles, matchups, names,
    use_spieltyp=False, own_double_pairs=None, stronger_double_pair=1,
    doubles_stats=None, own_on_letters=None, own_is_home=None,
    started=None,
):
    """Spread across singles orders, doubles pair splits, and Spiel-5 vs Spiel-10 assignment.

    When own_double_pairs is set (UI edit / fixed doubles), only that pair split is
    considered — matching what the user can change (24 singles × 2 Spiel-5/10 slots).
  """
    if own_double_pairs:
        pair_a, pair_b = _normalize_own_double_pairs(own, own_double_pairs, profiles)
        pair_partitions = [(pair_a, pair_b)]
    else:
        pair_partitions = _all_double_pair_partitions(own)
    doubles_stats = doubles_stats or {}
    relevant = set(own)
    for _, order in scenarios:
        relevant.update(order)

    orientations = [True, False] if own_is_home is None else [bool(own_is_home)]
    schedule_by_orientation = {
        home: _schedule_for_orientation(
            home if own_on_letters is None else own_on_letters
        )
        for home in orientations
    }
    matchup_by_orientation = {
        home: _build_matchup_table(
            relevant, profiles, matchups, home, use_spieltyp=use_spieltyp,
        )
        for home in orientations
    }

    budget_started = started if started is not None else time.monotonic()
    wins = []
    for part_a, part_b in pair_partitions:
        for game5_pair, game10_pair in ((part_a, part_b), (part_b, part_a)):
            fixed_game_pairs = (tuple(game5_pair), tuple(game10_pair))
            caches_by_orientation = {
                home: _build_scenario_doubles_cache(
                    scenarios, part_a, part_b, profiles, doubles_stats,
                    stronger_double_pair, fixed_game_pairs,
                )
                for home in orientations
            }
            for own_order in permutations(own):
                orientation_wins = []
                for home in orientations:
                    _check_analysis_budget(budget_started)
                    _, agg, _, _, _, _ = _evaluate_lineup_for_perm(
                        own_order,
                        caches_by_orientation[home],
                        matchup_by_orientation[home],
                        schedule_by_orientation[home],
                        profiles=profiles,
                        matchups=matchups,
                        own_is_home=home,
                        use_spieltyp=use_spieltyp,
                    )
                    orientation_wins.append(agg['win'])
                wins.append(sum(orientation_wins) / len(orientations))

    if not wins:
        return None
    return round((max(wins) - min(wins)) * 100, 2)


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


def _format_match_score_display(
    win_prob,
    expected_own,
    expected_opp,
    *,
    loss_prob=None,
    draw_prob=None,
):
    """Show the expected winner's TT score, not the draw probability."""
    win_prob = float(win_prob or 0)
    own = float(expected_own or 0)
    opp = float(expected_opp or 0)
    loss_prob = float(loss_prob) if loss_prob is not None else None
    draw_prob = float(draw_prob) if draw_prob is not None else None

    favor_us = own >= opp + 0.25 or (
        loss_prob is not None
        and draw_prob is not None
        and win_prob > loss_prob
        and win_prob > draw_prob
        and own >= opp
    )
    favor_them = opp >= own + 0.25 or (
        loss_prob is not None
        and draw_prob is not None
        and loss_prob > win_prob
        and loss_prob > draw_prob
        and opp >= own
    )

    if favor_us:
        opp_r = int(opp)
        if opp_r <= 0 and opp < 0.5:
            return '10:0'
        if opp_r <= 1 and opp < 1.5:
            return '9:1'
        return f'8:{max(2, min(6, opp_r))}'

    if favor_them:
        own_r = int(own)
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
    rows = db.execute(text(f"""
        SELECT DISTINCT mp.external_player_id::text AS player_id
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND {_MATCH_DAY_FILTER}
          AND ((m.home_team = :team AND mp.side = 'home') OR (m.away_team = :team AND mp.side = 'away'))
    """), {'team': team, 'cutoff': cutoff}).scalars().all()
    return {str(pid) for pid in rows}


def _raw_team_lineup_scenarios(db, team, required_ids=None, ref_date=None, opponent_pool=None):
    ref_date = ref_date or _reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    rounds_map = _build_team_match_rounds_ago(db, team, ref_date)
    opponent_pool = opponent_pool or _load_opponent_pool(db, team, ref_date)
    rows = db.execute(text(f"""
        SELECT m.id AS match_id, m.match_date, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))
          AND mp.external_player_id IS NOT NULL
          AND {_MATCH_DAY_FILTER}
        ORDER BY m.match_date DESC NULLS LAST, m.id DESC
    """), {'team': team, 'cutoff': stats_cutoff}).mappings()
    matches = defaultdict(list)
    for row in rows:
        matches[row['match_id']].append(row)
    counts = Counter(); names = {}
    required = set(str(x) for x in required_ids) if required_ids is not None else None
    for match_id, players in matches.items():
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
            counts[tuple(order)] += _lineup_recency_weight(
                players[0].get('match_date'), ref_date,
                match_id=match_id, rounds_map=rounds_map,
            )
    total = sum(counts.values())
    if not total:
        return [], names
    common = counts.most_common(24)
    top_total = sum(count for _, count in common)
    if not top_total:
        return [], names
    return [(count / top_total, order) for order, count in common], names


def _known_quartet_lineup_scenarios(db, player_ids, ref_date=None, team=None):
    """Historical position orders for an exact known quartet (optionally team-scoped)."""
    ref_date = ref_date or _reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    rounds_map = (
        _build_team_match_rounds_ago(db, team, ref_date)
        if team
        else _build_match_rounds_ago(db, ref_date)
    )
    ids = [str(x) for x in player_ids]
    bind_names = [f'known_id_{i}' for i in range(len(ids))]
    id_params = {name: value for name, value in zip(bind_names, ids)}
    placeholders = ','.join(f':{name}' for name in bind_names)
    team_clause = ""
    params = {'cutoff': stats_cutoff, **id_params}
    if team:
        team_clause = (
            "AND ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))"
        )
        params['team'] = team
    rows = db.execute(text(f"""
        SELECT m.id AS match_id, m.match_date, mp.side, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE mp.external_player_id IS NOT NULL
          AND mp.external_player_id::text IN ({placeholders})
          {team_clause}
          AND {_MATCH_DAY_FILTER}
        ORDER BY m.match_date DESC NULLS LAST, m.id DESC
    """), params).mappings()
    matches = defaultdict(list)
    names = {}
    required = set(ids)
    for row in rows:
        matches[row['match_id']].append(row)
        names.setdefault(str(row['player_id']), row['player_name'])

    counts = Counter()
    for match_id, players in matches.items():
        by_side = defaultdict(dict)
        for row in players:
            by_side[row['side']][str(row['player_id'])] = row
        for side_players in by_side.values():
            if set(side_players) != required:
                continue
            order = [None] * 4
            valid = True
            for pid in ids:
                idx = _position_index(side_players[pid]['position'])
                if idx is None or order[idx] is not None:
                    valid = False
                    break
                order[idx] = pid
            if valid and all(order):
                counts[tuple(order)] += _lineup_recency_weight(
                    players[0].get('match_date'), ref_date,
                    match_id=match_id, rounds_map=rounds_map,
                )

    total = sum(counts.values())
    if not total:
        return [], names, 0
    return [(count / total, order) for order, count in counts.most_common(24)], names, total


def _load_player_position_priors(db, player_ids, ref_date=None, team=None):
    """Recency-weighted, smoothed per-player singles position rates (team-scoped or cross-team)."""
    ref_date = ref_date or _reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    rounds_map = _build_match_rounds_ago(db, ref_date)
    ids = [str(x) for x in player_ids]
    bind_names = [f'pid_{i}' for i in range(len(ids))]
    id_params = {name: value for name, value in zip(bind_names, ids)}
    placeholders = ','.join(f':{name}' for name in bind_names)
    team_clause = ""
    params = {'cutoff': stats_cutoff, **id_params}
    if team:
        team_clause = (
            "AND ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))"
        )
        params['team'] = team
    rows = db.execute(text(f"""
        SELECT mp.external_player_id::text AS player_id, mp.position,
               m.id AS match_id, m.match_date
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND mp.external_player_id::text IN ({placeholders})
          {team_clause}
          AND {_MATCH_DAY_FILTER}
    """), params).mappings()
    counts = {pid: [0.0, 0.0, 0.0, 0.0] for pid in ids}
    raw_counts = {pid: [0, 0, 0, 0] for pid in ids}
    for row in rows:
        pid = str(row['player_id'])
        idx = _position_index(row['position'])
        if pid in counts and idx is not None:
            weight = _lineup_recency_weight(
                row.get('match_date'), ref_date,
                match_id=row.get('match_id'), rounds_map=rounds_map,
            )
            counts[pid][idx] += weight
            raw_counts[pid][idx] += 1
    priors = {}
    for pid in ids:
        weighted = counts[pid]
        raw = raw_counts[pid]
        total_raw = sum(raw)
        total_weighted = sum(weighted)
        # More Dirichlet smoothing when few distinct observations (F12).
        alpha_scale = math.sqrt(4.0 / max(1.0, float(total_raw)))
        alpha = POSITION_PRIOR_SMOOTHING * alpha_scale
        denom = total_weighted + 4 * alpha
        priors[pid] = [(x + alpha) / denom for x in weighted]
    return priors


def _measure_global_strength_lineup_support(db, ref_date=None):
    """How often strongest RC is on A/1 and weakest on D/4 across all four-player lineups."""
    ref_date = ref_date or _reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    cache_key = str(stats_cutoff)
    cached = _global_strength_support_cache.get(cache_key)
    if cached is not None:
        return cached
    rows = db.execute(text(f"""
        WITH side_players AS (
            SELECT m.id AS match_id, mp.side, mp.external_player_id::text AS player_id, mp.position
            FROM xttv_matches m
            JOIN match_players mp ON mp.match_id = m.id
            WHERE mp.external_player_id IS NOT NULL
              AND {_MATCH_DAY_FILTER}
        ),
        fours AS (
            SELECT match_id, side
            FROM side_players
            GROUP BY match_id, side
            HAVING count(DISTINCT player_id) = 4
        )
        SELECT sp.match_id, sp.side, sp.player_id, sp.position
        FROM side_players sp
        JOIN fours f ON f.match_id = sp.match_id AND f.side = sp.side
    """), {'cutoff': stats_cutoff}).mappings()
    by_lineup = defaultdict(dict)
    player_ids: set[str] = set()
    for row in rows:
        idx = _position_index(row['position'])
        if idx is None:
            continue
        pid = str(row['player_id'])
        by_lineup[(row['match_id'], row['side'])][idx] = pid
        player_ids.add(pid)
    rc_map = _load_latest_rc_map(db, list(player_ids))
    strongest_on_top = 0
    weakest_on_bottom = 0
    total = 0
    for slots in by_lineup.values():
        if len(slots) != 4:
            continue
        order = [slots[i] for i in range(4)]
        rc_vals = [_resolve_rc_rating(pid, rc_map, order) for pid in order]
        strongest_idx = max(range(4), key=lambda i: rc_vals[i])
        weakest_idx = min(range(4), key=lambda i: rc_vals[i])
        if strongest_idx == 0:
            strongest_on_top += 1
        if weakest_idx == 3:
            weakest_on_bottom += 1
        total += 1
    if total < GLOBAL_STRENGTH_MIN_SAMPLE:
        result = (0.0, 0.0, 0.0, total)
    else:
        top_rate = strongest_on_top / total
        bottom_rate = weakest_on_bottom / total
        support = (top_rate + bottom_rate) / 2.0
        result = (support, top_rate, bottom_rate, total)
    _global_strength_support_cache[cache_key] = result
    return result


def _source_includes_strength_prior(source: str | None) -> bool:
    return bool(source and 'strength-prior' in source)


def _measure_strength_lineup_support(db, player_ids, ref_date=None):
    """Legacy per-quartet strength-lineup rates (diagnostics only)."""
    ref_date = ref_date or _reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    ids = [str(x) for x in player_ids]
    bind_names = [f'spid_{i}' for i in range(len(ids))]
    id_params = {name: value for name, value in zip(bind_names, ids)}
    placeholders = ','.join(f':{name}' for name in bind_names)
    rows = db.execute(text(f"""
        WITH side_players AS (
            SELECT m.id AS match_id, mp.side, mp.external_player_id::text AS player_id, mp.position
            FROM xttv_matches m
            JOIN match_players mp ON mp.match_id = m.id
            WHERE mp.external_player_id IS NOT NULL
              AND mp.external_player_id::text IN ({placeholders})
              AND {_MATCH_DAY_FILTER}
        ),
        fours AS (
            SELECT match_id, side
            FROM side_players
            GROUP BY match_id, side
            HAVING count(DISTINCT player_id) = 4
        )
        SELECT sp.match_id, sp.side, sp.player_id, sp.position
        FROM side_players sp
        JOIN fours f ON f.match_id = sp.match_id AND f.side = sp.side
    """), {'cutoff': stats_cutoff, **id_params}).mappings()
    rc_map = _load_latest_rc_map(db, ids)
    by_lineup = defaultdict(dict)
    for row in rows:
        idx = _position_index(row['position'])
        if idx is None:
            continue
        by_lineup[(row['match_id'], row['side'])][idx] = str(row['player_id'])
    strongest_on_top = 0
    weakest_on_bottom = 0
    total = 0
    for slots in by_lineup.values():
        if len(slots) != 4:
            continue
        order = [slots[i] for i in range(4)]
        rc_vals = [_resolve_rc_rating(pid, rc_map, order) for pid in order]
        strongest_idx = max(range(4), key=lambda i: rc_vals[i])
        weakest_idx = min(range(4), key=lambda i: rc_vals[i])
        if strongest_idx == 0:
            strongest_on_top += 1
        if weakest_idx == 3:
            weakest_on_bottom += 1
        total += 1
    if total < 3:
        return 0.0, 0.0, 0.0, total
    top_rate = strongest_on_top / total
    bottom_rate = weakest_on_bottom / total
    support = (top_rate + bottom_rate) / 2.0
    return support, top_rate, bottom_rate, total


def _adaptive_strength_position_weights(player_ids, rc_by_player):
    """Monotone A>=B>=C>=D slot weights derived from RC gaps within the quartet."""
    rc_values = sorted(
        _resolve_rc_rating(pid, rc_by_player, player_ids) for pid in player_ids
    )
    spread = rc_values[-1] - rc_values[0]
    if spread < STRENGTH_RC_SPREAD_FLAT_THRESHOLD:
        return STRENGTH_POSITION_WEIGHTS_TIGHT
    gaps = [rc_values[i + 1] - rc_values[i] for i in range(3)]
    total_gap = sum(gaps) or 1.0
    normalized = [gap / total_gap for gap in gaps]
    w_min = STRENGTH_POSITION_WEIGHT_MIN
    w_span = STRENGTH_POSITION_WEIGHT_MAX - STRENGTH_POSITION_WEIGHT_MIN
    # Top gap (2nd-strongest vs strongest) raises A; bottom gap lowers D.
    w_a = w_min + w_span * (0.25 + 0.75 * normalized[2])
    w_d = w_min + w_span * (0.25 + 0.75 * (1.0 - normalized[0]))
    w_b = w_min + w_span * (0.25 + 0.75 * (normalized[2] + normalized[1]) / 2.0)
    w_c = w_min + w_span * (0.25 + 0.75 * (normalized[0] + normalized[1]) / 2.0)
    w_b = min(w_a, w_b)
    w_c = min(w_b, w_c)
    w_d = min(w_c, w_d)
    return (w_a, w_b, w_c, w_d)


def _scenarios_from_strength_prior(player_ids, rc_by_player):
    players = [str(x) for x in player_ids]
    position_weights = _adaptive_strength_position_weights(players, rc_by_player)
    weighted = []
    for order in permutations(players):
        score = sum(
            position_weights[pos] * _resolve_rc_rating(pid, rc_by_player, players)
            for pos, pid in enumerate(order)
        )
        weighted.append((math.exp(score / STRENGTH_RC_SCALE), tuple(order)))
    total = sum(p for p, _ in weighted)
    if total <= 0:
        uniform = 1.0 / 24.0
        return [(uniform, tuple(o)) for o in permutations(players)]
    scenarios = [(p / total, o) for p, o in weighted]
    scenarios.sort(key=lambda item: (-item[0], item[1]))
    return scenarios


def _scenarios_from_position_priors(player_ids, position_priors):
    players = [str(x) for x in player_ids]
    uniform = 1.0 / 24.0
    weighted = []
    for order in permutations(players):
        prob = 1.0
        for pos, pid in enumerate(order):
            rates = position_priors.get(pid, [0.25, 0.25, 0.25, 0.25])
            prob *= rates[pos]
        weighted.append((prob, tuple(order)))
    total = sum(p for p, _ in weighted)
    if total <= 0:
        return [(uniform, tuple(o)) for o in permutations(players)]
    return [(p / total, o) for p, o in weighted]


def _lineup_cohesion(db, player_ids, ref_date=None):
    """Return raw and recency-weighted recurrence for quartet and best trio."""
    actual = {str(x) for x in player_ids}
    if len(actual) != 4:
        return 0, 0.0, 0, 0.0
    ref_date = ref_date or _reference_date(db)
    cache_key = (str(_cutoff(ref_date, STATS_YEARS)), ','.join(sorted(actual)))
    cached = _lineup_cohesion_cache.get(cache_key)
    if cached is not None:
        return cached
    rounds_map = _build_match_rounds_ago(db, ref_date)
    rows = db.execute(text(f"""
        SELECT m.id AS match_id, m.match_date, mp.side, mp.external_player_id::text AS player_id
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND {_MATCH_DAY_FILTER}
    """), {'cutoff': _cutoff(ref_date, STATS_YEARS)}).mappings()
    exact = 0
    exact_mass = 0.0
    trio_counts = Counter()
    trio_masses = Counter()
    by_lineup = {}
    for row in rows:
        key = (row['match_id'], row['side'])
        entry = by_lineup.setdefault(key, {'players': set(), 'match_date': row['match_date'], 'match_id': row['match_id']})
        entry['players'].add(str(row['player_id']))
    for entry in by_lineup.values():
        lineup = entry['players']
        overlap = lineup & actual
        weight = _lineup_recency_weight(
            entry['match_date'], ref_date,
            match_id=entry.get('match_id'), rounds_map=rounds_map,
        )
        if len(overlap) == 4:
            exact += 1
            exact_mass += weight
        if len(overlap) >= 3:
            for trio in combinations(sorted(overlap), 3):
                trio_counts[trio] += 1
                trio_masses[trio] += weight
    best_trio = max(trio_counts, key=trio_counts.get) if trio_counts else None
    result = (
        exact,
        exact_mass,
        trio_counts.get(best_trio, 0),
        trio_masses.get(best_trio, 0.0),
    )
    _lineup_cohesion_cache[cache_key] = result
    return result


def _global_strength_weight_scale(db, ref_date=None):
    """Scale strength-prior trust from measured league-wide RC-on-A/D support (F10)."""
    support, _, _, total = _measure_global_strength_lineup_support(db, ref_date)
    if total < GLOBAL_STRENGTH_MIN_SAMPLE:
        return 1.0
    min_support = (GLOBAL_STRENGTH_MIN_TOP_RATE + GLOBAL_STRENGTH_MIN_BOTTOM_RATE) / 2.0
    if support >= min_support + 0.03:
        return 1.0
    if support <= min_support - 0.02:
        return 0.4
    span = 0.05
    return 0.4 + max(0.0, (support - (min_support - 0.02)) / span) * 0.6


def _adaptive_strength_weight(db, player_ids, ref_date=None):
    """Strength weight based on recurrence of the four-player group."""
    ref_date = ref_date or _reference_date(db)
    exact, exact_mass, trio, trio_mass = _lineup_cohesion(db, player_ids, ref_date)
    if exact >= COHESIVE_QUARTET_MIN_MATCHES and exact_mass >= COHESIVE_QUARTET_MIN_RECENCY_MASS:
        strength_weight = 0.15
    elif trio >= COHESIVE_TRIO_MIN_MATCHES and trio_mass >= COHESIVE_TRIO_MIN_RECENCY_MASS:
        strength_weight = 0.30
    elif exact >= MEDIUM_GROUP_MIN_MATCHES and exact_mass >= 2.0:
        strength_weight = 0.40
    elif exact == 0 and trio == 0:
        strength_weight = 0.80
    elif exact <= 1 and trio <= 1:
        strength_weight = 0.80
    else:
        strength_weight = 0.70
    strength_weight *= _global_strength_weight_scale(db, ref_date)
    return min(0.80, max(0.0, strength_weight)), exact, trio


def _normalize_scenario_map(scenario_map):
    total = sum(scenario_map.values())
    if total <= 0:
        return {}
    return {order: prob / total for order, prob in scenario_map.items()}


def _blend_scenario_maps(joint_map, prior_map, joint_weight):
    joint_weight = max(0.0, min(1.0, joint_weight))
    merged = {}
    for order in set(joint_map) | set(prior_map):
        merged[order] = (
            joint_weight * joint_map.get(order, 0.0)
            + (1.0 - joint_weight) * prior_map.get(order, 0.0)
        )
    return _normalize_scenario_map(merged)


def _blend_known_quartet_scenarios(joint_scenarios, joint_n, prior_scenarios):
    if joint_n <= 0 or not joint_scenarios:
        return prior_scenarios, 0.0
    joint_weight = joint_n / (joint_n + KNOWN_QUARTET_JOINT_PRIOR_STRENGTH)
    joint_map = _normalize_scenario_map({order: prob for prob, order in joint_scenarios})
    prior_map = {order: prob for prob, order in prior_scenarios}
    blended = _blend_scenario_maps(joint_map, prior_map, joint_weight)
    scenarios = sorted(
        [(prob, order) for order, prob in blended.items()],
        key=lambda item: (-item[0], item[1]),
    )
    return scenarios, joint_weight


def _load_known_quartet_joint_scenarios(db, player_ids, ref_date):
    """Joint historical orders for an exact quartet, aggregated across all teams."""
    actual = [str(x) for x in player_ids]
    scenarios, names, joint_n = _known_quartet_lineup_scenarios(db, actual, ref_date)
    return scenarios, joint_n, names, 'raw-cross-team'


def _apply_strength_prior_to_scenarios(scenarios, db, ref_date):
    """Blend historical orders with a recurrence-adaptive strength prior."""
    if not scenarios:
        return scenarios, None
    by_set = defaultdict(list)
    for probability, order in scenarios:
        by_set[frozenset(order)].append((probability, tuple(order)))
    rc_map = _load_latest_rc_map(db, list({pid for group in by_set for pid in group}))
    blended_all = []
    for player_set, group in by_set.items():
        players = list(player_set)
        group_total = sum(probability for probability, _ in group)
        if group_total <= 0:
            continue
        hist_map = _normalize_scenario_map({order: probability for probability, order in group})
        strength_scenarios = _scenarios_from_strength_prior(players, rc_map)
        strength_map = {order: probability for probability, order in strength_scenarios}
        strength_weight, exact_n, trio_n = _adaptive_strength_weight(db, players, ref_date)
        inner = _blend_scenario_maps(hist_map, strength_map, 1.0 - strength_weight)
        for order, inner_prob in inner.items():
            blended_all.append((group_total * inner_prob, order))
    total = sum(probability for probability, _ in blended_all)
    if total <= 0:
        return scenarios, None
    result = [(probability / total, order) for probability, order in blended_all]
    result.sort(key=lambda item: (-item[0], item[1]))
    meta = 'strength-prior-adaptive(cohesion=exact4/trio3)'
    return result, meta


def _build_combined_prior_scenarios(db, player_ids, ref_date, strength_weight=None):
    """Position priors blended with an adaptive RC strength-lineup prior."""
    actual = [str(x) for x in player_ids]
    position_priors = _load_player_position_priors(db, actual, ref_date)
    position_scenarios = _scenarios_from_position_priors(actual, position_priors)
    if strength_weight is None:
        strength_weight, exact_n, trio_n = _adaptive_strength_weight(db, actual, ref_date)
    else:
        exact_n, _, trio_n, _ = _lineup_cohesion(db, actual, ref_date)
    if strength_weight <= 0:
        return position_scenarios, 'known-opponent-position-prior-cross-team'
    rc_map = _load_latest_rc_map(db, actual)
    strength_scenarios = _scenarios_from_strength_prior(actual, rc_map)
    prior_map = {order: prob for prob, order in position_scenarios}
    strength_map = {order: prob for prob, order in strength_scenarios}
    blended = _blend_scenario_maps(prior_map, strength_map, 1.0 - strength_weight)
    scenarios = sorted(
        [(prob, order) for order, prob in blended.items()],
        key=lambda item: (-item[0], item[1]),
    )
    return scenarios, (
        f'known-opponent-position-strength-prior-cross-team'
        f'(strength-w={strength_weight:.2f},exact4={exact_n},best3={trio_n})'
    )


def _build_known_four_opponent_scenarios(db, opponent_team, player_ids, ref_date):
    """Blend cross-team quartet history with position/strength priors when joint data is thin."""
    actual = [str(x) for x in player_ids]
    joint_scenarios, joint_n, fallback_names, joint_source = _load_known_quartet_joint_scenarios(
        db, actual, ref_date,
    )
    strength_weight, exact_n, trio_n = _adaptive_strength_weight(db, actual, ref_date)
    prior_scenarios, prior_source = _build_combined_prior_scenarios(
        db, actual, ref_date, strength_weight=strength_weight,
    )
    if joint_n <= 0:
        return prior_scenarios, fallback_names, prior_source
    # The same recency-aware strength weight controls how much a concrete
    # quartet history can dominate the strength/position prior.
    joint_weight = 1.0 - strength_weight
    joint_map = _normalize_scenario_map({order: prob for prob, order in joint_scenarios})
    prior_map = {order: prob for prob, order in prior_scenarios}
    scenarios = sorted(
        [(prob, order) for order, prob in _blend_scenario_maps(
            joint_map, prior_map, joint_weight,
        ).items()],
        key=lambda item: (-item[0], item[1]),
    )
    return scenarios, fallback_names, (
        f'known-opponent-blended-{joint_source}'
        f'(history-w={joint_weight:.2f},exact4={exact_n},best3={trio_n})'
    )


def _build_partial_opponent_scenarios(db, team, known_ids, opponent_pool, ref_date):
    known = {str(x) for x in known_ids}
    remaining = 4 - len(known)
    names = {}
    if remaining < 0:
        return [], names
    candidates = sorted(opponent_pool - known)
    if len(candidates) < remaining:
        quartet = _pick_quartet_from_pool(db, opponent_pool | known, ref_date, prefer=list(known))
        if quartet:
            scenarios = _scenarios_from_strength_prior_lineup(db, quartet)
            return scenarios, names
        return [], names
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
    stats_stmt = text(f"""
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
              AND {_MATCH_DAY_FILTER}
            UNION ALL
            SELECT ap.external_player_id::text, ap.name, 'away',
                   CASE WHEN split_part(trim(g.result),':',2)::int > split_part(trim(g.result),':',1)::int THEN 1 ELSE 0 END
            FROM match_games g
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND ap.external_player_id::text IN :ids
              AND {_MATCH_DAY_FILTER}
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
            'rc_trend': None,
            'rc_trend_momentum': None,
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
               snap.rc_deviation,
               snap.observed_at
        FROM xttv_players xp
        JOIN LATERAL (
            SELECT rc_rating, rc_deviation, observed_at
            FROM player_rating_snapshots
            WHERE player_id = xp.id
              AND source = 'ratingscentral'
            ORDER BY observed_at DESC
            LIMIT 1
        ) snap ON true
        WHERE xp.external_player_id::text IN :ids
    """).bindparams(bindparam('ids', expanding=True))
    for r in db.execute(rc_stmt, params).mappings():
        pid = str(r['player_id'])
        profiles.setdefault(pid, _empty_profile())
        raw_rc = float(r['rc_rating']) if r['rc_rating'] is not None else None
        deviation = float(r['rc_deviation']) if r.get('rc_deviation') is not None else None
        profiles[pid]['rc_deviation'] = deviation
        profiles[pid]['rc_rating'] = _effective_rc(raw_rc, deviation)

    # Load the statistics horizon; _compute_trend_metrics narrows this to the
    # date span of the latest <=25 singles (not to a calendar year).
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
    for r in db.execute(
        trend_stmt,
        {
            **params,
            'cutoff': trend_cutoff,
        },
    ).mappings():
        trend_rows[str(r['player_id'])].append({'observed_at': r['observed_at'], 'rc_rating': r['rc_rating']})

    recent_singles_stmt = text(f"""
        WITH all_singles AS (
            SELECT hp.external_player_id::text AS player_id,
                   {_SQL_MATCH_DAY} AS match_day,
                   split_part(trim(g.result),':',1)::int AS own_score,
                   split_part(trim(g.result),':',2)::int AS opp_score
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids
              AND {_MATCH_DAY_FILTER}
            UNION ALL
            SELECT ap.external_player_id::text,
                   {_SQL_MATCH_DAY},
                   split_part(trim(g.result),':',2)::int,
                   split_part(trim(g.result),':',1)::int
            FROM match_games g
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND ap.external_player_id::text IN :ids
              AND {_MATCH_DAY_FILTER}
        ),
        ranked AS (
            SELECT player_id, own_score, opp_score, match_day,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY match_day DESC) AS rn
            FROM all_singles
        ),
        bounded AS (
            SELECT player_id, own_score, opp_score, match_day
            FROM ranked r
            WHERE r.match_day >= (
                SELECT r25.match_day
                FROM ranked r25
                WHERE r25.player_id = r.player_id AND r25.rn = 25
            )
            OR NOT EXISTS (
                SELECT 1 FROM ranked r25
                WHERE r25.player_id = r.player_id AND r25.rn = 25
            )
        )
        SELECT player_id, own_score, opp_score, match_day
        FROM bounded
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
        net_change, component = _compute_trend_metrics(snapshots, recent_singles_rows.get(pid, []))
        profiles[pid]['rc_trend'] = net_change
        profiles[pid]['rc_trend_momentum'] = net_change
        profiles[pid]['trend_component'] = component

    h2h_stmt = text(f"""
        WITH base AS (
            SELECT hp.external_player_id::text AS home_id, ap.external_player_id::text AS away_id,
                   CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS home_win
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids AND ap.external_player_id::text IN :ids
              AND {_MATCH_DAY_FILTER}
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
    _impute_missing_rc_ratings(profiles, ids)
    return names, profiles, matchups


def _load_latest_rc_map(db, ids):
    """Latest Ratings Central RC per player, aligned with the team player picker."""
    ids = [str(x) for x in ids]
    if not ids:
        return {}
    stmt = text("""
        SELECT xp.external_player_id::text AS player_id,
               snap.rc_rating,
               snap.rc_deviation
        FROM xttv_players xp
        JOIN LATERAL (
            SELECT rc_rating, rc_deviation
            FROM player_rating_snapshots
            WHERE player_id = xp.id AND source = 'ratingscentral'
            ORDER BY observed_at DESC
            LIMIT 1
        ) snap ON true
        WHERE xp.external_player_id::text IN :ids
    """).bindparams(bindparam('ids', expanding=True))
    out = {}
    for row in db.execute(stmt, {'ids': ids}).mappings():
        effective = _effective_rc(row['rc_rating'], row.get('rc_deviation'))
        if effective is not None:
            out[str(row['player_id'])] = effective
    return out


def _rc_values_for_ids(player_ids, latest_rc_map, profiles):
    """Collect RC values for an ordered player list, preferring latest snapshots."""
    values = []
    for pid in player_ids:
        pid = str(pid)
        rc = latest_rc_map.get(pid)
        if rc is None:
            rc = _rc_value(profiles.get(pid, _empty_profile()))
        if rc is not None:
            values.append(rc)
    return values


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

    style_stmt = text(f"""
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
              AND {_MATCH_DAY_FILTER}
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
              AND {_MATCH_DAY_FILTER}
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


def _filter_scenarios(scenarios, opponent_pool, allow_outside=None):
    allow_outside = {str(x) for x in (allow_outside or [])}
    effective_pool = {str(x) for x in opponent_pool} | allow_outside
    filtered = []
    for probability, order in scenarios:
        if set(order).issubset(effective_pool):
            filtered.append((probability, order))
    if not filtered:
        return []
    total = sum(probability for probability, _ in filtered)
    if total <= 0:
        return filtered
    return [(probability / total, order) for probability, order in filtered]


def _apply_opponent_fallback(db, scenarios, source, opponent_pool, ref_date, actual, warnings):
    if scenarios:
        return scenarios, source, warnings
    fallback_scenarios, fallback_source, fallback_warnings = _build_fallback_opponent_scenarios(
        db, opponent_pool, ref_date, actual=actual,
    )
    warnings.extend(fallback_warnings)
    return fallback_scenarios, fallback_source, warnings


def _sharpen_scenarios(scenarios, alpha=SCENARIO_SHARPENING_ALPHA):
    """Concentrate scenario mass on likelier opponent lineups (power reweighting)."""
    if not scenarios or alpha <= 1.0 or len(scenarios) <= 1:
        return scenarios
    weighted = [(probability ** alpha, order) for probability, order in scenarios]
    total = sum(probability for probability, _ in weighted)
    if total <= 0:
        return scenarios
    sharpened = [(probability / total, order) for probability, order in weighted]
    sharpened.sort(key=lambda item: (-item[0], item[1]))
    return sharpened


def _load_analysis_data(own, opponent_team, actual, use_spieltyp=False):
    db = SessionLocal()
    warnings = []
    try:
        db.execute(text("SET statement_timeout = '15000ms'")); db.execute(text("SET lock_timeout = '500ms'"))
        own = [str(x) for x in own]; actual = None if actual is None else [str(x) for x in actual]
        ref_date = _reference_date(db)
        opponent_pool = _load_opponent_pool(db, opponent_team, ref_date)
        if actual:
            opponent_pool = _extend_opponent_pool(opponent_pool, actual)
        fallback_names = {}
        if actual is not None and len(actual) > 0:
            if len(actual) == 4:
                scenarios, fallback_names, source = _build_known_four_opponent_scenarios(
                    db, opponent_team, actual, ref_date,
                )
            else:
                scenarios, fallback_names = _raw_team_lineup_scenarios(db, opponent_team, actual, ref_date, opponent_pool); source = 'known-opponent-historical-raw'
                if not scenarios:
                    scenarios, fallback_names = _build_partial_opponent_scenarios(db, opponent_team, actual, opponent_pool, ref_date); source = 'known-opponent-combination-fallback'
        else:
            scenarios, fallback_names = _raw_team_lineup_scenarios(
                db, opponent_team, None, ref_date, opponent_pool,
            )
            source = 'predicted-historical-recency-weighted'
        scenarios, source, warnings = _apply_opponent_fallback(
            db, scenarios, source, opponent_pool, ref_date, actual, warnings,
        )
        if not scenarios:
            return {}, {}, {}, [], source, ref_date, opponent_pool, warnings
        if not _source_includes_strength_prior(source):
            scenarios, strength_meta = _apply_strength_prior_to_scenarios(scenarios, db, ref_date)
            if strength_meta:
                source = f'{source}+{strength_meta}'
        if source != 'all-24-uniform-fallback':
            filtered = _filter_scenarios(scenarios, opponent_pool, allow_outside=actual)
            if not filtered and scenarios:
                warnings.append(
                    'Aufstellungshistorie enthielt Spieler außerhalb des Gegner-Kaders — Fallback wird verwendet.',
                )
                scenarios, source, warnings = _apply_opponent_fallback(
                    db, [], source, opponent_pool, ref_date, actual, warnings,
                )
            else:
                scenarios = filtered
        if not scenarios:
            return {}, {}, {}, [], source, ref_date, opponent_pool, warnings
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
        return names, profiles, matchups, scenarios, source, ref_date, opponent_pool, warnings
    except Exception as exc:
        warnings.append(
            f'Daten konnten nicht vollständig geladen werden ({type(exc).__name__}). '
            'Bitte XTTV-Import und Datenbankverbindung prüfen.',
        )
        return {}, {}, {}, [], 'load-error', date.today(), set(), warnings
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
            profiles=profiles, matchups=matchups, own_is_home=own_is_home,
            use_spieltyp=use_spieltyp,
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
            'expected_score_display': _format_match_score_display(
                agg['win'],
                agg['expected_own_wins'],
                agg['expected_opponent_wins'],
                loss_prob=agg['loss'],
                draw_prob=agg['draw'],
            ),
            'recommended_doubles_on': placement,
            'doubles_win_probability_on_5': round(win5, 6),
            'doubles_win_probability_on_10': round(win10, 6),
        })
        if time.monotonic() - started > MAX_ANALYSIS_SECONDS:
            raise RuntimeError('Analysis exceeded the internal 7-second safety budget')
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
                loss_prob=(home_item['team_loss_probability'] + away_item['team_loss_probability']) / 2.0,
                draw_prob=(home_item['team_draw_probability'] + away_item['team_draw_probability']) / 2.0,
            ),
            'recommended_doubles_on': home_item.get('recommended_doubles_on', 5) if home_item['team_win_probability'] >= away_item['team_win_probability'] else away_item.get('recommended_doubles_on', 5),
            'doubles_win_probability_on_5': round((home_item.get('doubles_win_probability_on_5', 0) + away_item.get('doubles_win_probability_on_5', 0)) / 2.0, 6),
            'doubles_win_probability_on_10': round((home_item.get('doubles_win_probability_on_10', 0) + away_item.get('doubles_win_probability_on_10', 0)) / 2.0, 6),
        })
    return merged


def _explain_recommendation(
    own_order, scenarios, matchup_p, profiles, names, evaluated, own_is_home=None,
    own_double_pairs=None, stronger_double_pair=1, doubles_stats=None,
    recommended_doubles_on=5, own_on_letters=None, fixed_game_pairs=None,
    matchups=None, use_spieltyp=False,
):
    """Create a human-readable, model-grounded explanation for the top lineup."""
    weighted_games = [0.0] * TOTAL_GAMES
    weighted_double = [0.0, 0.0]
    position_rates = {pid: [0.0] * 4 for pid in own_order}
    pair_a, pair_b = _normalize_own_double_pairs(own_order, own_double_pairs, profiles)

    matchups = matchups or {}
    schedule = _schedule_for_orientation(
        (True if own_is_home is not False else False) if own_on_letters is None else own_on_letters
    )

    for scenario_probability, opp_order in scenarios:
        _, game_probs, doubles = _scenario_match_outcome(
            own_order, opp_order, schedule, matchup_p, pair_a, pair_b, profiles, doubles_stats or {},
            recommended_doubles_on, stronger_double_pair,
            fixed_game_pairs=fixed_game_pairs,
            matchups=matchups, own_is_home=bool(own_is_home) if own_is_home is not None else True,
            use_spieltyp=use_spieltyp,
        )
        played = _game_play_probabilities(game_probs)
        for i, p in enumerate(game_probs):
            weighted_games[i] += scenario_probability * p
        for i, p in enumerate(doubles):
            weighted_double[i] += scenario_probability * p

        for pid in own_order:
            for position in range(4):
                rate = sum(
                    _lookup_matchup_probability(
                        matchup_p, pid, opp_order[opp_idx], profiles, matchups,
                        own_is_home=bool(own_is_home) if own_is_home is not None else True,
                        use_spieltyp=use_spieltyp,
                    ) * played[game_index]
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

    strongest_name = names.get(strongest[0], f'Spieler {strongest[0]}')
    bullets.append(
        f"{strongest_name} hat in seiner empfohlenen Position die höchste durchschnittliche "
        f"Einzelspielchance der vier ({strongest[1] * 100:.1f} %)."
    )

    if best_game[1] - worst_game[1] >= 0.03:
        bullets.append(
            f"Entscheidend im erwarteten Spielverlauf: Spiel {best_game[0]} mit ca. "
            f"{best_game[1] * 100:.0f} % Siegchance, Spiel {worst_game[0]} mit ca. "
            f"{worst_game[1] * 100:.0f} % — die Aufstellung nutzt genau diese Unterschiede."
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
            # These are the scenario-aggregated game probabilities used for
            # the result display. Keeping them alongside the explanation
            # avoids presenting marginal matchup values as one fixed match.
            'aggregate_game_probabilities': [round(value, 6) for value in weighted_games],
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
    matchups=None, use_spieltyp=False,
):
    """Expected singles wins, weighted by the probability each game is played."""
    matchups = matchups or {}
    profiles = profiles or {}
    schedule = _schedule_for_orientation(bool(own_is_home) if own_on_letters is None else own_on_letters)
    expected = {pid: 0.0 for pid in own_order}
    pair_a, pair_b = _normalize_own_double_pairs(own_order, own_double_pairs, profiles)
    for scenario_probability, opp_order in scenarios:
        singles = [
            _lookup_matchup_probability(
                matchup_p, own_order[own_idx], opp_order[opp_idx],
                profiles, matchups, own_is_home=bool(own_is_home), use_spieltyp=use_spieltyp,
            )
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
    matchups=None, use_spieltyp=False,
):
    """Per-player singles matchups with played and conditional win probability."""
    matchups = matchups or {}
    profiles = profiles or {}
    schedule = _schedule_for_orientation(bool(own_is_home) if own_on_letters is None else own_on_letters)
    breakdown = {pid: [] for pid in own_order}
    pair_a, pair_b = _normalize_own_double_pairs(own_order, own_double_pairs, profiles)
    for game_idx, (own_idx, opp_idx) in enumerate(schedule):
        pid = own_order[own_idx]
        weighted_prob = 0.0
        opponent_weights = defaultdict(float)
        for scenario_probability, opp_order in scenarios:
            opp_id = opp_order[opp_idx]
            opponent_weights[opp_id] += scenario_probability
            weighted_prob += scenario_probability * _lookup_matchup_probability(
                matchup_p, pid, opp_id, profiles, matchups,
                own_is_home=bool(own_is_home), use_spieltyp=use_spieltyp,
            )
        play_probability = 0.0
        for scenario_probability, opp_order in scenarios:
            singles = [
                _lookup_matchup_probability(
                    matchup_p, own_order[o], opp_order[a], profiles, matchups,
                    own_is_home=bool(own_is_home), use_spieltyp=use_spieltyp,
                )
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
        opponent_weighted = [
            {
                'player_id': oid,
                'name': names.get(oid, f'Spieler {oid}'),
                'weight': round(weight, 4),
            }
            for oid, weight in sorted(opponent_weights.items(), key=lambda item: -item[1])
        ]
        is_mix = len(opponent_weighted) > 1
        if is_mix:
            opponent_display = ' · '.join(
                f"{_player_short_name(entry['name'])} {entry['weight'] * 100:.0f} %"
                for entry in opponent_weighted[:4]
            )
            if len(opponent_weighted) > 4:
                opponent_display += ' · …'
        else:
            opponent_display = opponent_weighted[0]['name'] if opponent_weighted else '—'
        breakdown[pid].append({
            'game_number': SINGLE_GAME_NUMBERS[game_idx],
            'opponent_display': opponent_display,
            'opponent_scenario_mix': is_mix,
            'opponent_weighted': opponent_weighted,
            'win_probability': round(weighted_prob, 3),
            'played_probability': round(play_probability, 6),
        })
    return breakdown


def _player_short_name(full_name: str) -> str:
    parts = (full_name or '').strip().split()
    return parts[-1] if parts else full_name


def _h2h_summaries_for_player(pid, breakdown, matchups, names):
    seen = set()
    summaries = []
    for matchup in breakdown:
        for entry in matchup.get('opponent_weighted', []):
            opp_id = entry.get('player_id')
            if not opp_id or opp_id in seen:
                continue
            seen.add(opp_id)
            wins, games = matchups.get((pid, opp_id), (0, 0))
            if games:
                summaries.append({
                    'opponent_player_id': opp_id,
                    'opponent_name': names.get(opp_id, f'Spieler {opp_id}'),
                    'wins': wins,
                    'losses': games - wins,
                })
    return summaries


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
    own_double_pairs=None, stronger_double_pair=1, doubles_stats=None, actual_opponent_ids=None,
    use_spieltyp=False,
):
    own_order = recommendation['own_player_ids']
    expected_singles = {}
    singles_breakdown = {}
    if matchup_p is not None and own_is_home is not None:
        expected_singles = _expected_singles_wins_for_lineup(
            own_order, scenarios, matchup_p, own_is_home, own_on_letters,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
            matchups=matchups, use_spieltyp=use_spieltyp,
        )
        singles_breakdown = _expected_singles_breakdown_for_lineup(
            own_order, scenarios, matchup_p, names, own_is_home, own_on_letters,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
            matchups=matchups, use_spieltyp=use_spieltyp,
        )
    elif matchup_p is not None and own_is_home is None:
        schedule_orientation = True if own_on_letters is None else own_on_letters
        home = _expected_singles_wins_for_lineup(
            own_order, scenarios, matchup_p, True, schedule_orientation,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
            matchups=matchups, use_spieltyp=use_spieltyp,
        )
        away_matchup = _build_matchup_table(
            set(own_order) | {pid for _, order in scenarios for pid in order},
            profiles,
            matchups,
            False,
        )
        away = _expected_singles_wins_for_lineup(
            own_order, scenarios, away_matchup, False, schedule_orientation,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
            matchups=matchups, use_spieltyp=use_spieltyp,
        )
        expected_singles = {pid: (home[pid] + away[pid]) / 2.0 for pid in own_order}
        home_breakdown = _expected_singles_breakdown_for_lineup(
            own_order, scenarios, matchup_p, names, True, schedule_orientation,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
            matchups=matchups, use_spieltyp=use_spieltyp,
        )
        away_breakdown = _expected_singles_breakdown_for_lineup(
            own_order, scenarios, away_matchup, names, False, schedule_orientation,
            own_double_pairs, profiles, doubles_stats, stronger_double_pair,
            recommendation.get('recommended_doubles_on', 5),
            matchups=matchups, use_spieltyp=use_spieltyp,
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
            'rc_trend': round(profile['rc_trend'], 1) if profile.get('rc_trend') is not None else None,
            'singles_wins': profile.get('wins', 0),
            'singles_games': profile.get('games', 0),
            'expected_singles_wins': exp_rounded,
            'expected_singles_wins_raw': exp_raw,
            'expected_singles_matchups': breakdown,
            'h2h_summaries': _h2h_summaries_for_player(pid, breakdown, matchups, names),
        })

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

    if actual_opponent_ids and len(actual_opponent_ids) == 4:
        opponent_rc_ids = [str(x) for x in actual_opponent_ids]
    elif scenarios:
        opponent_rc_ids = list(sorted(scenarios, key=lambda item: -item[0])[0][1])
    else:
        opponent_rc_ids = []

    with SessionLocal() as db:
        latest_rc = _load_latest_rc_map(db, list(own) + opponent_rc_ids)

    own_rc_values = _rc_values_for_ids(own, latest_rc, profiles)
    top_rc_values = _rc_values_for_ids(opponent_rc_ids, latest_rc, profiles)

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
    lineup_spread_pp = (recommendation or {}).get('lineup_spread_pp')
    if len(evaluated) > 1:
        top_ranking = evaluated[0].get('ranking_team_win_probability', evaluated[0]['team_win_probability'])
        second_ranking = evaluated[1].get('ranking_team_win_probability', evaluated[1]['team_win_probability'])
        margin_pp = round((top_ranking - second_ranking) * 100, 2)

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
        'opponent_rc_count': len(top_rc_values),
        'opponent_weighted_rc_avg': weighted_avg,
        'rc_gap_vs_top_lineup': round(own_avg - top_avg, 1) if own_avg is not None and top_avg is not None else None,
        'own_players': own_players,
        'opponent_team': opponent_team,
        'top_lineup_margin_pp': margin_pp,
        'lineup_spread_pp': lineup_spread_pp,
        'h2h_pairs_with_data': len(h2h_pairs),
        'h2h_pairs': sorted(h2h_pairs, key=lambda x: -x['games'])[:8],
        'expected_first_doubles_probability': detail.get('first_doubles_probability'),
        'expected_second_doubles_probability': detail.get('second_doubles_probability'),
        'reference_date': ref_date.isoformat(),
        'stats_window_years': STATS_YEARS,
        'opponent_pool_years': OPPONENT_POOL_YEARS,
        'opponent_pool_size': len(opponent_pool),
        'scenario_variants': len(scenarios),
        'scenario_sharpening_alpha': SCENARIO_SHARPENING_ALPHA,
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

    names, profiles, matchups, scenarios, source, ref_date, opponent_pool, data_warnings = _load_analysis_data(
        own, opponent_team, actual, use_spieltyp=use_spieltyp,
    )
    warnings = list(data_warnings)
    if not scenarios:
        phase = 'C' if actual and len(actual) == 4 and opponent_on_letters is not None else ('B' if actual else 'A')
        if not warnings:
            warnings.append(
                f'Keine passende Gegner-Aufstellung für {opponent_team} gefunden. '
                'Prüfe Teamname, XTTV-Import und ob mindestens vier Gegenspieler im Kader sind.',
            )
        return {
            'ok': True,
            'phase': phase,
            'recommendations': [],
            'warnings': warnings,
            'opponent_team': opponent_team,
            'data_quality': {
                'opponent_pool_size': len(opponent_pool),
                'opponent_set_source': source,
                'reference_date': ref_date.isoformat() if hasattr(ref_date, 'isoformat') else str(ref_date),
            },
        }

    display_scenarios = scenarios
    scenarios = _sharpen_scenarios(scenarios)

    opp_ids = set()
    for _, order in scenarios:
        opp_ids.update(order)
    try:
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
    except RuntimeError as exc:
        if 'safety budget' not in str(exc).lower():
            raise
        phase = 'C' if actual and len(actual) == 4 and opponent_on_letters is not None else ('B' if actual else 'A')
        warnings.append(
            'Die Analyse wurde wegen Zeitlimit abgebrochen (große Datenmenge). '
            'Bitte erneut versuchen oder weniger bekannte Gegner angeben.',
        )
        return {
            'ok': True,
            'phase': phase,
            'recommendations': [],
            'warnings': warnings,
            'opponent_team': opponent_team,
            'data_quality': {
                'opponent_pool_size': len(opponent_pool),
                'opponent_set_source': source,
                'reference_date': ref_date.isoformat() if hasattr(ref_date, 'isoformat') else str(ref_date),
            },
        }

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
        matchups=matchups, use_spieltyp=use_spieltyp,
    )
    for item in evaluated:
        item['ranking_team_win_probability'] = item['team_win_probability']
    optimal_ranking = evaluated[0]['ranking_team_win_probability']
    for item in evaluated:
        item['loss_pp_vs_optimal'] = round(
            max(0.0, (optimal_ranking - item['ranking_team_win_probability']) * 100),
            2,
        )
    strength_order = _trivial_strength_own_order(own, profiles)
    strength_item = next(
        (item for item in evaluated if item['own_player_ids'] == strength_order),
        None,
    )
    advantage_vs_strength_pp = None
    if strength_item is not None:
        advantage_vs_strength_pp = round(
            max(0.0, (optimal_ranking - strength_item['ranking_team_win_probability']) * 100),
            2,
        )
    recommendation['advantage_vs_strength_lineup_pp'] = advantage_vs_strength_pp
    recommendation['strength_lineup_player_ids'] = strength_order
    try:
        recommendation['lineup_spread_pp'] = _compute_lineup_configuration_spread_pp(
            own, scenarios, profiles, matchups, names,
            use_spieltyp=use_spieltyp, own_double_pairs=own_double_pairs,
            stronger_double_pair=stronger_double_pair, doubles_stats=doubles_stats,
            own_on_letters=own_on_letters, own_is_home=own_is_home,
            started=started,
        )
    except RuntimeError as exc:
        if 'safety budget' not in str(exc).lower():
            raise
        recommendation['lineup_spread_pp'] = None
        warnings.append('Konfigurations-Spread konnte wegen Zeitlimit nicht berechnet werden.')
    # The lineup ranking remains unchanged. For the result card, however,
    # use the same scenario-aggregated 14-game vector shown by the
    # explanation instead of aggregating separately from marginal matchups.
    display_probs = explanation.get('detail', {}).get('aggregate_game_probabilities')
    if display_probs and len(display_probs) == TOTAL_GAMES:
        display_dist = _team_result_distribution(display_probs)
        recommendation['team_win_probability'] = round(display_dist['win'], 6)
        recommendation['team_draw_probability'] = round(display_dist['draw'], 6)
        recommendation['team_loss_probability'] = round(display_dist['loss'], 6)
        recommendation['expected_own_wins'] = round(display_dist['expected_own_wins'], 3)
        recommendation['expected_opponent_wins'] = round(display_dist['expected_opponent_wins'], 3)
        recommendation['expected_score_display'] = _format_match_score_display(
            display_dist['win'],
            display_dist['expected_own_wins'],
            display_dist['expected_opponent_wins'],
            loss_prob=display_dist['loss'],
            draw_prob=display_dist['draw'],
        )
    opponent_doubles_by_order = {}
    opponent_predictions = [
        {
            'player_ids': list(order),
            'players': _players_for_ids(order, names),
            'probability': round(probability, 6),
            'model_probability': round(probability, 6),
            'doubles': _cached_opponent_doubles(order, doubles_stats, profiles, names, opponent_doubles_by_order),
        }
        for probability, order in display_scenarios
    ]
    opponent_predictions.sort(key=lambda item: (-item['probability'], item['player_ids']))
    elapsed = time.monotonic() - started
    info_summary = _build_info_summary(
        own, scenarios, profiles, names, matchups, recommendation, evaluated, explanation,
        opponent_team, ref_date, opponent_pool, source, orientation_note,
        matchup_p=matchup_p, own_is_home=own_is_home, own_on_letters=own_on_letters,
        own_double_pairs=own_double_pairs, stronger_double_pair=stronger_double_pair,
        doubles_stats=doubles_stats, actual_opponent_ids=actual, use_spieltyp=use_spieltyp,
    )

    return {
        'ok': True,
        'phase': 'C' if actual and len(actual) == 4 and opponent_on_letters is not None else ('B' if actual else 'A'),
        'warnings': warnings,
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
                f'time-weighted net-level RC trend (last {TREND_MAX_SNAPSHOTS} RC snapshots; '
                f'max ±{TREND_MAX_COMPONENT:.2f}) + '
                f'venue adjustment (max ±{HOME_AWAY_MAX_COMPONENT:.2f})'
            ),
            'rc_baseline': (
                f'{RC_BASELINE:.0f} fixed neutral midpoint: snapshots have no league association, '
                'so no leakage-safe league baseline can be derived from the available schema'
            ),
            'h2h_weight': (
                f'blend of shrinkage toward RC base ((w + {H2H_SHRINKAGE_MATCHES:.0f}·p_base) / (g + {H2H_SHRINKAGE_MATCHES:.0f})) '
                f'and legacy weighted direct record ({H2H_SHRINKAGE_BLEND:.0%} legacy weight)'
            ),
            'home_away': (
                f'overall record is the base; smoothed home/away delta is separate and capped at ±{HOME_AWAY_MAX_COMPONENT:.2f} '
                f'after {HOME_AWAY_MIN_GAMES} venue games ({HOME_AWAY_MIN_OVERALL_GAMES} overall games required)'
            ),
            'opponent_lineups': (
                f'historical position orders from last {STATS_YEARS} years with recurrence-adaptive RC '
                f'strength-lineup prior (global RC-on-A/D support from measured league data); '
                f'recency by league rounds (half-life {LINEUP_RECENCY_HALF_LIFE_MATCHES:.0f} matches); '
                f'player pool last {OPPONENT_POOL_YEARS} years'
            ),
            'scenario_sharpening_alpha': SCENARIO_SHARPENING_ALPHA,
            'player_stats': f'XTTV singles + RC snapshots, last {STATS_YEARS} years',
            'orientation': orientation_note,
        },
        'data_quality': {
            'scenario_variants': len(scenarios),
            'scenario_sharpening_alpha': SCENARIO_SHARPENING_ALPHA,
            'lineup_recency_half_life_matches': LINEUP_RECENCY_HALF_LIFE_MATCHES,
            'own_orders_evaluated': 24,
            'reference_date': ref_date.isoformat(),
            'stats_window_years': STATS_YEARS,
            'opponent_pool_years': OPPONENT_POOL_YEARS,
            'opponent_pool_size': len(opponent_pool),
            'runtime_seconds': round(elapsed, 4),
            'runtime_data_source': 'rc-profiles-plus-lineup-history',
            'missing_player_stats_use_neutral_prior': source in ('all-24-uniform-fallback', 'strength-prior-fallback', 'fallback-unavailable', 'load-error'),
            'position_probabilities_observed': source != 'all-24-uniform-fallback',
        },
    }
