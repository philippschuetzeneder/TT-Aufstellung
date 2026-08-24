from app.analysis_service import (
    _apply_strength_prior_to_scenarios,
    _measure_global_strength_lineup_support,
    _reference_date,
    _strength_prior_blend_weight,
    STRENGTH_PRIOR_BLEND_WEIGHT,
    _lineup_recency_weight,
)
from app.db import SessionLocal


def test_global_strength_lineup_is_frequent_in_database():
    with SessionLocal() as db:
        support, top_rate, bottom_rate, total = _measure_global_strength_lineup_support(db)
    assert total >= 100
    assert top_rate >= 0.52
    assert bottom_rate >= 0.50


def test_strength_prior_uses_fixed_blend_weight():
    blend_weight, support, top_rate, bottom_rate, total = _strength_prior_blend_weight()
    assert blend_weight == STRENGTH_PRIOR_BLEND_WEIGHT
    assert support is None
    assert top_rate is None
    assert bottom_rate is None
    assert total is None


def test_apply_strength_prior_uses_recurrence_adaptive_weight():
    historical = [(1.0, ('a', 'b', 'c', 'd'))]
    with SessionLocal() as db:
        ref_date = _reference_date(db)
        blended, meta = _apply_strength_prior_to_scenarios(historical, db, ref_date)
    assert meta and 'strength-prior-adaptive' in meta
    assert abs(sum(probability for probability, _ in blended) - 1.0) < 1e-9
    assert len(blended) == 24


def test_recent_lineup_is_weighted_more_than_two_year_old_lineup():
    recent = _lineup_recency_weight('24.08.2026', __import__('datetime').date(2026, 8, 24))
    old = _lineup_recency_weight('24.08.2024', __import__('datetime').date(2026, 8, 24))
    assert recent > old * 100
