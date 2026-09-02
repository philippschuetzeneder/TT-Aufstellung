"""Tests for alternative lineup loss display fields."""

from app.analysis_service import analyze_lineup


def _attach_loss_pp(evaluated: list[dict]) -> None:
    for item in evaluated:
        item['ranking_team_win_probability'] = item['team_win_probability']
    optimal_ranking = evaluated[0]['ranking_team_win_probability']
    for item in evaluated:
        item['loss_pp_vs_optimal'] = round(
            max(0.0, (optimal_ranking - item['ranking_team_win_probability']) * 100),
            2,
        )


def test_loss_pp_vs_optimal_reflects_ranking_gap():
    evaluated = [
        {'team_win_probability': 0.568, 'rank': 1},
        {'team_win_probability': 0.562, 'rank': 2},
        {'team_win_probability': 0.555, 'rank': 3},
    ]
    _attach_loss_pp(evaluated)
    assert evaluated[0]['loss_pp_vs_optimal'] == 0.0
    assert evaluated[1]['loss_pp_vs_optimal'] == 0.6
    assert evaluated[2]['loss_pp_vs_optimal'] == 1.3


def test_analyze_lineup_includes_loss_pp_on_alternatives():
    result = analyze_lineup(
        ['24890', '24889', '23782', '21773'],
        'Sandl 1',
        None,
        opponent_limit=24,
        own_team='Tragwein/Kamig 3',
        opponent_on_letters=True,
    )
    if not result.get('ok') or result.get('phase') != 'B':
        return
    recs = result.get('recommendations') or []
    if len(recs) < 2:
        return
    assert recs[1].get('loss_pp_vs_optimal') is not None
    margin = result.get('info_summary', {}).get('top_lineup_margin_pp')
    if margin is not None:
        assert recs[1]['loss_pp_vs_optimal'] == round(float(margin), 2)


def test_analyze_lineup_includes_advantage_vs_strength_lineup():
    result = analyze_lineup(
        ['24890', '24889', '23782', '21773'],
        'Sandl 1',
        None,
        opponent_limit=24,
        own_team='Tragwein/Kamig 3',
        opponent_on_letters=True,
    )
    if not result.get('ok'):
        return
    rec = result.get('recommendation') or {}
    assert rec.get('advantage_vs_strength_lineup_pp') is not None
    assert rec.get('strength_lineup_player_ids') is not None
    assert len(rec['strength_lineup_player_ids']) == 4
    assert rec['advantage_vs_strength_lineup_pp'] >= 0
    assert rec.get('lineup_spread_pp') is not None
    assert rec['lineup_spread_pp'] >= 0


def test_lineup_spread_includes_doubles_and_pair_partitions():
    """Spread must exceed singles-only range when doubles assignment matters."""
    from app.analysis_service import _compute_lineup_configuration_spread_pp, _load_analysis_data, _load_doubles_stats, _sharpen_scenarios, SessionLocal
    import time

    own = ['21773', '23754', '24889', '23782']
    opp = ['22472', '22223', '21339', '21970']
    pairs = [['21773', '24889'], ['23754', '23782']]
    names, profiles, matchups, scenarios, source, ref_date, opponent_pool, _warnings = _load_analysis_data(
        own, 'Alberndorf 2', opp, use_spieltyp=False,
    )
    with SessionLocal() as db:
        opp_ids = set()
        for _, order in scenarios:
            opp_ids.update(order)
        doubles_stats = _load_doubles_stats(
            db, own, opp_ids, 'Tragwein/Kamig 3', 'Alberndorf 2', ref_date,
        )
    scenarios = _sharpen_scenarios(scenarios)
    started = time.monotonic()
    spread = _compute_lineup_configuration_spread_pp(
        own, scenarios, profiles, matchups, names,
        use_spieltyp=False, own_double_pairs=pairs, stronger_double_pair=1,
        doubles_stats=doubles_stats, own_on_letters=False, own_is_home=True,
    )
    assert spread is not None
    assert spread > 0.5

    result = analyze_lineup(
        own, 'Alberndorf 2', opp, own_is_home=True, opponent_on_letters=True,
        own_team='Tragwein/Kamig 3', own_double_pairs=pairs, stronger_double_pair=1,
    )
    assert result['recommendation']['lineup_spread_pp'] == spread


def test_lineup_spread_covers_doubles_swap_delta():
    """Spread over editable configs must be >= deviation from swapping Spiel 5/10 doubles."""
    own = ['21773', '23754', '24890', '24889']
    opp = ['22472', '22223', '21339', '21970']
    pairs = [['21773', '24889'], ['24890', '23754']]
    optimal = analyze_lineup(
        own, 'Alberndorf 2', opp, own_is_home=True, opponent_on_letters=True,
        own_team='Tragwein/Kamig 3', own_double_pairs=pairs, stronger_double_pair=1,
    )
    if not optimal.get('ok'):
        return
    rec = optimal['recommendation']
    order = rec['own_player_ids']
    spread_pp = rec.get('lineup_spread_pp')
    assert spread_pp is not None
    swapped_pairs = [pairs[1], pairs[0]]
    swapped = analyze_lineup(
        own, 'Alberndorf 2', opp, own_is_home=True, opponent_on_letters=True,
        own_team='Tragwein/Kamig 3', own_double_pairs=swapped_pairs, stronger_double_pair=1,
        fixed_own_order=order, fixed_doubles_on=5,
    )
    delta_pp = abs(
        (swapped['recommendation']['team_win_probability'] - rec['team_win_probability']) * 100,
    )
    assert spread_pp >= delta_pp - 0.05


def test_sharpen_scenarios_concentrates_probability():
    from app.analysis_service import _sharpen_scenarios

    scenarios = [
        (0.10, ('a', 'b', 'c', 'd')),
        (0.07, ('b', 'a', 'c', 'd')),
        (0.05, ('c', 'd', 'a', 'b')),
    ]
    sharpened = _sharpen_scenarios(scenarios, alpha=2.5)
    assert sharpened[0][0] > scenarios[0][0]
    assert round(sum(probability for probability, _ in sharpened), 6) == 1.0


def test_phase_c_lineup_spread_with_scenario_sharpening():
    own = ['21773', '23754', '24890', '24889']
    opp = ['22472', '22223', '21339', '21970']
    pairs = [['21773', '24889'], ['24890', '23754']]
    result = analyze_lineup(
        own, 'Alberndorf 2', opp, own_is_home=True, opponent_on_letters=True,
        own_team='Tragwein/Kamig 3', own_double_pairs=pairs, stronger_double_pair=1,
    )
    if not result.get('ok'):
        return
    recs = result.get('recommendations') or []
    if len(recs) < 2:
        return
    spread_pp = result['recommendation'].get('lineup_spread_pp')
    assert spread_pp is not None
    assert spread_pp >= 1.0
    assert result['model']['scenario_sharpening_alpha'] == 2.5
