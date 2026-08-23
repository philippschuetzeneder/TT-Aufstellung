from app.analysis_service import (
    _apply_strength_prior_to_scenarios,
    _measure_global_strength_lineup_support,
    _reference_date,
    _strength_prior_blend_weight,
    STRENGTH_PRIOR_BLEND_WEIGHT,
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


def test_apply_strength_prior_runs_when_global_pattern_holds():
    historical = [(1.0, ('a', 'b', 'c', 'd'))]
    with SessionLocal() as db:
        ref_date = _reference_date(db)
        blended, meta = _apply_strength_prior_to_scenarios(historical, db, ref_date)
    assert meta and 'strength-prior-fixed' in meta
    assert 'w=0.30' in meta
    assert sum(probability for probability, _ in blended) == 1.0
    assert len(blended) == 24
