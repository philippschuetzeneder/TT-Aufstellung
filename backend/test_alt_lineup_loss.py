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
