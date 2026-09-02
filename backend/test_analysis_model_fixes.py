"""Regression tests for analysis model fixes (F1, F3, F5, F6, F7, F10, F11, F12)."""
from unittest.mock import MagicMock, patch

from app.analysis_service import (
    H2H_SHRINKAGE_BLEND,
    H2H_SHRINKAGE_MATCHES,
    LINEUP_RECENCY_HALF_LIFE_MATCHES,
    SCENARIO_SHARPENING_ALPHA,
    _adaptive_strength_position_weights,
    _empty_profile,
    _effective_rc,
    _global_strength_weight_scale,
    _h2h_blended_probability,
    _h2h_legacy_probability,
    _h2h_shrunk_probability,
    _impute_missing_rc_ratings,
    _lineup_recency_weight_by_rounds,
    _load_player_position_priors,
    _lookup_matchup_probability,
    _matchup_probability,
    _scenarios_from_strength_prior,
    _sharpen_scenarios,
)


def test_strength_position_weights_are_monotone():
    rc = {'p1': 1500.0, 'p2': 1490.0, 'p3': 1480.0, 'p4': 1300.0}
    weights = _adaptive_strength_position_weights(['p1', 'p2', 'p3', 'p4'], rc)
    assert weights[0] >= weights[1] >= weights[2] >= weights[3]


def test_strength_prior_mode_matches_rc_descending():
    players = ['p1', 'p2', 'p3', 'p4']
    rc = {'p1': 1500.0, 'p2': 1490.0, 'p3': 1480.0, 'p4': 1300.0}
    scenarios = _scenarios_from_strength_prior(players, rc)
    expected = tuple(sorted(players, key=lambda pid: -rc[pid]))
    assert scenarios[0][1] == expected


def test_recency_by_rounds_halflife():
    assert abs(_lineup_recency_weight_by_rounds(0) - 1.0) < 1e-9
    assert abs(_lineup_recency_weight_by_rounds(LINEUP_RECENCY_HALF_LIFE_MATCHES) - 0.5) < 1e-9
    assert _lineup_recency_weight_by_rounds(0) > _lineup_recency_weight_by_rounds(8)


def test_h2h_shrinks_toward_model_base_not_fifty_percent():
    profiles = {
        'a': {**_empty_profile(), 'rc_rating': 1700.0, 'games': 50, 'wins': 30},
        'b': {**_empty_profile(), 'rc_rating': 1400.0, 'games': 50, 'wins': 25},
    }
    base = _matchup_probability('a', 'b', profiles, {}, own_is_home=True)
    assert base > 0.9
    one_loss = _matchup_probability('a', 'b', profiles, {('a', 'b'): (0, 1)}, own_is_home=True)
    assert one_loss > 0.68
    assert one_loss < base


def test_h2h_no_jump_at_first_game():
    profiles = {
        'a': {**_empty_profile(), 'rc_rating': 1600.0},
        'b': {**_empty_profile(), 'rc_rating': 1500.0},
    }
    p0 = _matchup_probability('a', 'b', profiles, {}, own_is_home=True)
    p1 = _matchup_probability('a', 'b', profiles, {('a', 'b'): (1, 1)}, own_is_home=True)
    expected = _h2h_blended_probability(1, 1, p0)
    assert abs(p1 - expected) < 1e-9


def test_h2h_one_game_sits_between_pure_shrinkage_and_legacy():
    base = 0.227
    wins, games = 0, 1
    shrunk = _h2h_shrunk_probability(wins, games, base)
    legacy = _h2h_legacy_probability(wins, games, base)
    blended = _h2h_blended_probability(wins, games, base)
    assert shrunk < blended < legacy or legacy < blended < shrunk
    assert abs(blended - (0.5 * shrunk + 0.5 * legacy)) < 1e-9


def test_production_sharpening_concentrates_scenarios():
    scenarios = [(0.10, ('a', 'b', 'c', 'd')), (0.07, ('b', 'a', 'c', 'd'))]
    sharpened = _sharpen_scenarios(scenarios, alpha=SCENARIO_SHARPENING_ALPHA)
    assert SCENARIO_SHARPENING_ALPHA > 1.0
    assert sharpened[0][0] > scenarios[0][0]
    assert round(sum(probability for probability, _ in sharpened), 6) == 1.0


def test_effective_rc_shrinks_high_deviation():
    tight = _effective_rc(1600.0, 30.0)
    loose = _effective_rc(1600.0, 120.0)
    assert tight > loose > 1400.0


def test_impute_missing_rc_uses_teammate_average():
    profiles = {
        'a': {**_empty_profile(), 'rc_rating': 1600.0},
        'b': {**_empty_profile(), 'rc_rating': 1400.0},
        'c': {**_empty_profile(), 'rc_rating': None},
    }
    _impute_missing_rc_ratings(profiles, ['a', 'b', 'c'])
    assert profiles['c']['rc_rating'] == 1500.0
    assert profiles['c']['rc_imputed'] is True


def test_lookup_matchup_probability_never_defaults_to_half():
    profiles = {
        'a': {**_empty_profile(), 'rc_rating': 1650.0},
        'b': {**_empty_profile(), 'rc_rating': 1350.0},
    }
    prob = _lookup_matchup_probability({}, 'a', 'b', profiles, {}, own_is_home=True)
    assert prob != 0.5
    assert prob > 0.5


def test_position_priors_use_round_recency_and_raw_counts():
    ref_date = __import__('datetime').date(2026, 3, 1)
    rows = [
        {'player_id': '1', 'position': 'A', 'match_date': '01.01.2024 19:00', 'match_id': 10},
        {'player_id': '1', 'position': 'D', 'match_date': '01.02.2026 19:00', 'match_id': 20},
    ]
    db = MagicMock()
    db.execute.return_value.mappings.return_value = rows
    rounds = {10: 12, 20: 0}
    with patch('app.analysis_service._build_match_rounds_ago', return_value=rounds):
        priors = _load_player_position_priors(db, ['1'], ref_date=ref_date)
    assert priors['1'][0] < priors['1'][3]


def test_global_strength_weight_scale_is_used():
    db = MagicMock()
    with patch(
        'app.analysis_service._measure_global_strength_lineup_support',
        return_value=(0.48, 0.46, 0.50, 200),
    ):
        assert _global_strength_weight_scale(db) == 0.4
