from itertools import permutations
from unittest.mock import MagicMock, patch

from app.analysis_service import (
    _build_fallback_opponent_scenarios,
    _clamp_probability,
    _extend_opponent_pool,
    _filter_scenarios,
    _parse_match_date,
    _pick_quartet_from_pool,
    _uniform_lineup_scenarios,
)


def test_parse_match_date_handles_missing_and_invalid():
    assert _parse_match_date(None) is None
    assert _parse_match_date('') is None
    assert _parse_match_date('ungültig') is None
    assert _parse_match_date('15.03.2025').isoformat() == '2025-03-15'


def test_clamp_probability_bounds():
    assert _clamp_probability(-0.2) == 0.0
    assert _clamp_probability(1.5) == 1.0
    assert _clamp_probability(float('nan')) == 0.0


def test_uniform_lineup_scenarios_sum_to_one():
    players = ['a', 'b', 'c', 'd']
    scenarios = _uniform_lineup_scenarios(players)
    assert len(scenarios) == 24
    assert abs(sum(p for p, _ in scenarios) - 1.0) < 1e-9


def test_uniform_lineup_rejects_invalid_quartet():
    assert _uniform_lineup_scenarios(['a', 'b', 'c']) == []
    assert _uniform_lineup_scenarios(['a', 'a', 'b', 'c']) == []


def test_extend_opponent_pool_includes_known_opponents():
    pool = _extend_opponent_pool({'1', '2'}, ['3', '4'])
    assert pool == {'1', '2', '3', '4'}


def test_filter_scenarios_allows_known_outside_pool():
    scenarios = [(1.0, ('9', '8', '7', '6'))]
    filtered = _filter_scenarios(scenarios, {'1', '2'}, allow_outside=['9', '8', '7', '6'])
    assert len(filtered) == 1


def test_filter_scenarios_empty_when_no_overlap():
    scenarios = [(1.0, ('9', '8', '7', '6'))]
    assert _filter_scenarios(scenarios, {'1', '2'}) == []


@patch('app.analysis_service._load_latest_rc_map', return_value={'3': 1800.0, '4': 1200.0, '5': 1500.0, '6': 1500.0})
def test_pick_quartet_prefers_known_then_rc(_mock_rc):
    db = MagicMock()
    quartet = _pick_quartet_from_pool(db, {'3', '4', '5', '6'}, prefer=['1', '2'])
    assert quartet == ['1', '2', '3', '5']


@patch(
    'app.analysis_service._scenarios_from_strength_prior_lineup',
    return_value=[(0.6, ('1', '2', '3', '4')), (0.4, ('2', '1', '3', '4'))],
)
def test_build_fallback_uses_strength_prior_when_rc_available(_mock_prior):
    db = MagicMock()
    scenarios, source, warnings = _build_fallback_opponent_scenarios(
        db, {'1', '2', '3', '4', '5'}, ref_date=None, actual=['1', '2', '3', '4'],
    )
    assert source == 'strength-prior-fallback'
    assert scenarios
    assert warnings


@patch('app.analysis_service._scenarios_from_strength_prior_lineup', return_value=[])
def test_build_fallback_uniform_when_no_rc(_mock_prior):
    db = MagicMock()
    scenarios, source, warnings = _build_fallback_opponent_scenarios(
        db, {'1', '2', '3', '4'}, ref_date=None, actual=['1', '2', '3', '4'],
    )
    assert source == 'all-24-uniform-fallback'
    assert len(scenarios) == 24
    assert warnings


def test_build_fallback_insufficient_pool():
    db = MagicMock()
    scenarios, source, warnings = _build_fallback_opponent_scenarios(
        db, {'1', '2'}, ref_date=None, actual=None,
    )
    assert scenarios == []
    assert source == 'fallback-unavailable'
    assert warnings


def test_clear_analysis_runtime_caches():
    from app.analysis_service import _lineup_cohesion_cache, _global_strength_support_cache, clear_analysis_runtime_caches

    _lineup_cohesion_cache[('k', 'v')] = (1, 1.0, 1, 1.0)
    _global_strength_support_cache['k'] = (1.0, 1.0, 1.0, 1)
    clear_analysis_runtime_caches()
    assert not _lineup_cohesion_cache
    assert not _global_strength_support_cache
