from datetime import datetime, timedelta

from app.analysis_service import (
    _compute_trend_metrics,
    _weighted_rc_momentum,
    TREND_MAX_COMPONENT,
)


def _snap_series(ratings):
    base = datetime(2025, 1, 1)
    return [
        {'observed_at': base + timedelta(days=index * 30), 'rc_rating': rating}
        for index, rating in enumerate(ratings)
    ]


def test_weighted_momentum_prefers_recent_gains():
    snapshots = _snap_series([1400, 1390, 1385, 1450])
    assert _weighted_rc_momentum(snapshots) > 0


def test_full_bonus_on_three_zero_sweeps():
    momentum, component = _compute_trend_metrics(
        _snap_series([1200, 1210]),
        [{'own_score': 3, 'opp_score': 0}, {'own_score': 3, 'opp_score': 0}, {'own_score': 3, 'opp_score': 0}],
    )
    assert component == TREND_MAX_COMPONENT


def test_full_bonus_on_recent_rc_surge():
    snapshots = _snap_series([1200, 1240, 1280, 1320])
    momentum, component = _compute_trend_metrics(snapshots, [])
    assert component == TREND_MAX_COMPONENT
    assert momentum > 0


def test_negative_year_but_recent_gain_can_be_positive():
    snapshots = _snap_series([1400, 1380, 1360, 1350, 1410])
    momentum, component = _compute_trend_metrics(snapshots, [])
    assert momentum > 0
