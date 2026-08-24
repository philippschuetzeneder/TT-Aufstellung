from datetime import datetime, timedelta

from app.analysis_service import (
    _compute_trend_metrics,
    _recent_singles_window,
    _trend_snapshot_window,
    _weighted_rc_momentum,
    TREND_MAX_COMPONENT,
    TREND_MAX_RC,
    TREND_MIN_RC,
)


def _snap_series(ratings):
    base = datetime(2025, 1, 1)
    return [
        {'observed_at': base + timedelta(days=index * 30), 'rc_rating': rating}
        for index, rating in enumerate(ratings)
    ]


def _singles(count, base=datetime(2025, 1, 1)):
    return [
        {'own_score': 3, 'opp_score': 1, 'match_day': base + timedelta(days=index * 30)}
        for index in range(count)
    ]


def test_weighted_momentum_prefers_recent_gains():
    snapshots = _snap_series([1400, 1390, 1385, 1450])
    assert _weighted_rc_momentum(snapshots) <= 0


def test_recent_gain_outweighs_same_old_gain():
    old = _snap_series([1400, 1450, 1400])
    old[1]['observed_at'] = old[0]['observed_at'] + timedelta(days=30)
    old[2]['observed_at'] = old[0]['observed_at'] + timedelta(days=365)
    fresh = _snap_series([1400, 1400, 1450])
    fresh[1]['observed_at'] = fresh[0]['observed_at'] + timedelta(days=30)
    fresh[2]['observed_at'] = fresh[0]['observed_at'] + timedelta(days=60)

    assert _weighted_rc_momentum(fresh) > _weighted_rc_momentum(old)


def test_intermediate_moves_are_not_additively_cumulative():
    snapshots = _snap_series([1400, 1450, 1400])

    trend = _weighted_rc_momentum(snapshots)
    assert trend == 0


def test_final_net_increase_remains_positive():
    assert _weighted_rc_momentum(_snap_series([1400, 1400, 1450])) > 0


def test_round_trip_returns_neutral_trend():
    trend, component = _compute_trend_metrics(
        _snap_series([100, 150, 100]), _singles(5)
    )
    assert trend == 0
    assert component == 0


def test_recent_net_change_dominates_older_net_change():
    singles = _singles(25)
    snapshots = [
        {'observed_at': datetime(2025, 1, 1), 'rc_rating': 100},
        {'observed_at': datetime(2026, 3, 26), 'rc_rating': 150},
        {'observed_at': datetime(2026, 5, 1), 'rc_rating': 250},
    ]
    trend, _ = _compute_trend_metrics(snapshots, singles)
    assert 0 < trend <= TREND_MAX_RC


def test_trend_supports_positive_and_negative_changes():
    assert _weighted_rc_momentum(_snap_series([1400, 1450])) > 0
    assert _weighted_rc_momentum(_snap_series([1400, 1350])) < 0


def test_invalid_snapshots_do_not_count_toward_minimum():
    snapshots = _snap_series([1400, None, 1450])
    trend, component = _compute_trend_metrics(snapshots, _singles(5))
    assert trend > 0
    assert component > 0


def test_trend_is_hard_bounded():
    positive = _weighted_rc_momentum(_snap_series([1400, 1600]))
    negative = _weighted_rc_momentum(_snap_series([1400, 1200]))
    assert 0 < positive <= TREND_MAX_RC
    assert TREND_MIN_RC <= negative < 0


def test_unsorted_snapshots_use_latest_observation_as_reference():
    snapshots = _snap_series([1400, 1450, 1500])

    assert _weighted_rc_momentum(list(reversed(snapshots))) == _weighted_rc_momentum(snapshots)


def test_three_zero_sweeps_do_not_override_net_signal():
    momentum, component = _compute_trend_metrics(
        _snap_series([1200, 1210]),
        [
            {'own_score': 3, 'opp_score': 0, 'match_day': datetime(2025, 1, 1)},
            {'own_score': 3, 'opp_score': 0, 'match_day': datetime(2025, 2, 1)},
            {'own_score': 3, 'opp_score': 0, 'match_day': datetime(2025, 3, 1)},
            {'own_score': 3, 'opp_score': 0, 'match_day': datetime(2025, 4, 1)},
            {'own_score': 3, 'opp_score': 0, 'match_day': datetime(2025, 5, 1)},
        ],
    )
    assert 0 < momentum <= 10
    assert 0 < component < TREND_MAX_COMPONENT


def test_full_bonus_on_recent_rc_surge():
    snapshots = _snap_series([1200, 1240, 1280, 1320])
    momentum, component = _compute_trend_metrics(snapshots, _singles(5))
    assert 0 < component <= TREND_MAX_COMPONENT
    assert momentum > 0


def test_robust_median_dampens_intermediate_level_outlier():
    snapshots = _snap_series([1400, 1380, 1360, 1350, 1410])
    momentum, component = _compute_trend_metrics(snapshots, _singles(5))
    assert momentum < 0
    assert component < 0


def test_display_trend_is_bounded_weighted_level_change():
    snapshots = _snap_series([1400, 1380, 1360, 1350, 1410])
    trend, _ = _compute_trend_metrics(snapshots, _singles(5))
    assert trend < 0


def test_display_trend_is_missing_for_short_series():
    trend, component = _compute_trend_metrics(_snap_series([1400, 1450]), _singles(4))
    assert trend is None
    assert component == 0.0


def test_five_singles_make_trend_calculable():
    trend, component = _compute_trend_metrics(_snap_series([1400, 1450]), _singles(5))
    assert trend is not None
    assert component > 0


def test_trend_uses_at_most_25_latest_singles():
    snapshots = _snap_series([1400, 1500, 1600])
    singles = _singles(30)
    selected = sorted(singles, key=lambda row: row['match_day'], reverse=True)[:25]
    snapshots[0]['observed_at'] = datetime(2025, 6, 1)
    snapshots[1]['observed_at'] = datetime(2025, 7, 1)
    snapshots[2]['observed_at'] = datetime(2025, 8, 1)
    trend, _ = _compute_trend_metrics(snapshots, singles)
    assert _trend_snapshot_window(snapshots, selected) == _trend_snapshot_window(snapshots, singles)
    assert trend is not None


def test_recent_window_keeps_all_games_on_25th_day():
    singles = _singles(24, base=datetime(2025, 2, 1))
    singles.extend([
        {'own_score': 3, 'opp_score': 1, 'match_day': datetime(2025, 1, 1)}
        for _ in range(3)
    ])
    window = _recent_singles_window(singles)
    assert len(window) == 27


def test_snapshots_are_stichtag_safe_and_include_earliest_selected_day():
    singles = _singles(5)
    snapshots = [
        {'observed_at': datetime(2024, 12, 31), 'rc_rating': 1300},
        {'observed_at': datetime(2025, 1, 1), 'rc_rating': 1400},
        {'observed_at': datetime(2025, 5, 1), 'rc_rating': 1500},
        {'observed_at': datetime(2025, 6, 1), 'rc_rating': 1600},
    ]
    window = _trend_snapshot_window(snapshots, singles)
    assert [row['rc_rating'] for row in window] == [1400, 1500]


def test_recent_segment_has_stronger_weight_than_old_segment():
    base = datetime(2025, 1, 1)
    singles = _singles(25, base=base)
    snapshots = [
        {'observed_at': base, 'rc_rating': 1400},
        {'observed_at': base + timedelta(days=30), 'rc_rating': 1450},
        {'observed_at': base + timedelta(days=700), 'rc_rating': 1500},
    ]
    trend, _ = _compute_trend_metrics(snapshots, singles)
    assert 0 < trend <= TREND_MAX_RC


def test_same_gain_is_more_valuable_when_recent():
    base = datetime(2025, 1, 1)
    snapshots = [
        {'observed_at': base, 'rc_rating': 100},
        {'observed_at': base + timedelta(days=30), 'rc_rating': 100},
        {'observed_at': base + timedelta(days=700), 'rc_rating': 150},
    ]
    assert _weighted_rc_momentum(
        snapshots, recent_boundary=base + timedelta(days=600)
    ) >= _weighted_rc_momentum(snapshots)


def test_small_current_net_change_is_about_ten():
    snapshots = [
        {'observed_at': datetime(2025, 1, 1), 'rc_rating': 1203},
        {'observed_at': datetime(2025, 5, 1), 'rc_rating': 1215},
    ]
    trend, _ = _compute_trend_metrics(snapshots, _singles(5))
    assert 5 <= trend <= 8


def test_recovered_intermediate_trough_cannot_dominate_small_net_decline():
    snapshots = [
        {'observed_at': datetime(2025, 1, 1), 'rc_rating': 1331},
        {'observed_at': datetime(2025, 2, 1), 'rc_rating': 1277},
        {'observed_at': datetime(2025, 3, 1), 'rc_rating': 1328},
    ]
    trend, _ = _compute_trend_metrics(snapshots, _singles(5))
    assert trend >= -6
