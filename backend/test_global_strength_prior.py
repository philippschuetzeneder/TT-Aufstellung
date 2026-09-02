from unittest.mock import MagicMock, patch

from app.analysis_service import (
    _apply_strength_prior_to_scenarios,
    _global_strength_weight_scale,
    _measure_global_strength_lineup_support,
    _reference_date,
    _lineup_recency_weight_by_rounds,
)
from app.db import SessionLocal


def test_global_strength_lineup_is_frequent_in_database():
    with SessionLocal() as db:
        support, top_rate, bottom_rate, total = _measure_global_strength_lineup_support(db)
    assert total >= 100
    assert top_rate >= 0.52
    assert bottom_rate >= 0.50


def test_apply_strength_prior_uses_recurrence_adaptive_weight():
    historical = [(1.0, ('a', 'b', 'c', 'd'))]
    with SessionLocal() as db:
        ref_date = _reference_date(db)
        blended, meta = _apply_strength_prior_to_scenarios(historical, db, ref_date)
    assert meta and 'strength-prior-adaptive' in meta
    assert abs(sum(probability for probability, _ in blended) - 1.0) < 1e-9
    assert len(blended) == 24


def test_recent_match_round_is_weighted_more_than_older_round():
    recent = _lineup_recency_weight_by_rounds(0)
    old = _lineup_recency_weight_by_rounds(16)
    assert recent > old
    assert abs(recent - 1.0) < 1e-9


def test_global_strength_weight_scale_returns_one_when_sample_small():
    db = MagicMock()
    with patch(
        'app.analysis_service._measure_global_strength_lineup_support',
        return_value=(0.0, 0.0, 0.0, 50),
    ):
        assert _global_strength_weight_scale(db) == 1.0
