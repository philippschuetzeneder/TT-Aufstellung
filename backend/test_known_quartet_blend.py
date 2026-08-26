from app.analysis_service import (
    KNOWN_QUARTET_JOINT_PRIOR_STRENGTH,
    _blend_known_quartet_scenarios,
    _scenarios_from_position_priors,
    _scenarios_from_strength_prior,
)


def _uniform_prior_scenarios(players):
    priors = {pid: [0.25, 0.25, 0.25, 0.25] for pid in players}
    return _scenarios_from_position_priors(players, priors)


def test_blend_never_100_percent_from_single_joint_observation():
    players = ['a', 'b', 'c', 'd']
    joint = [(1.0, ('a', 'b', 'c', 'd'))]
    prior = _uniform_prior_scenarios(players)
    scenarios, weight = _blend_known_quartet_scenarios(joint, 1, prior)
    top = scenarios[0]
    assert top[1] == ('a', 'b', 'c', 'd')
    assert top[0] < 1.0
    expected_weight = 1.0 / (1.0 + KNOWN_QUARTET_JOINT_PRIOR_STRENGTH)
    assert abs(weight - expected_weight) < 1e-9
    assert abs(sum(p for p, _ in scenarios) - 1.0) < 1e-9


def test_position_prior_only_when_no_joint_history():
    players = ['a', 'b', 'c', 'd']
    prior = _uniform_prior_scenarios(players)
    scenarios, weight = _blend_known_quartet_scenarios([], 0, prior)
    assert weight == 0.0
    assert len(scenarios) == 24
    assert all(abs(p - 1 / 24) < 1e-9 for p, _ in scenarios)


def test_joint_dominates_from_n5():
    players = ['a', 'b', 'c', 'd']
    joint = [(1.0, ('a', 'b', 'c', 'd'))]
    prior = _uniform_prior_scenarios(players)
    _, weight = _blend_known_quartet_scenarios(joint, 5, prior)
    assert weight >= 0.8


def test_strength_prior_prefers_strongest_on_top():
    players = ['weak', 'mid', 'strong', 'mid2']
    rc = {'strong': 2000.0, 'mid': 1600.0, 'mid2': 1550.0, 'weak': 1200.0}
    scenarios = _scenarios_from_strength_prior(players, rc)
    assert scenarios[0][1][0] == 'strong'


def test_adaptive_strength_weights_tight_gradient_when_rc_spread_small():
    from app.analysis_service import _adaptive_strength_position_weights, STRENGTH_POSITION_WEIGHTS_TIGHT

    players = ['a', 'b', 'c', 'd']
    rc = {'a': 1500.0, 'b': 1510.0, 'c': 1520.0, 'd': 1530.0}
    weights = _adaptive_strength_position_weights(players, rc)
    assert weights == STRENGTH_POSITION_WEIGHTS_TIGHT


def test_adaptive_strength_weights_emphasize_top_when_clear_leader():
    from app.analysis_service import _adaptive_strength_position_weights

    players = ['weak', 'mid', 'strong', 'mid2']
    rc = {'strong': 2000.0, 'mid': 1600.0, 'mid2': 1550.0, 'weak': 1200.0}
    w_a, w_b, w_c, w_d = _adaptive_strength_position_weights(players, rc)
    assert w_a > w_d
    assert w_a > 2.5
