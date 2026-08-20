from itertools import permutations

from app.analysis_service import (
    _empty_profile,
    _pair_probability,
    _team_result_probabilities,
    _matchup_probability,
)


def _profile(wins, games, rc=1500.0):
    profile = _empty_profile()
    profile.update({
        'wins': wins,
        'games': games,
        'home_wins': wins // 2,
        'home_games': max(games // 2, 1),
        'away_wins': wins - wins // 2,
        'away_games': max(games - games // 2, 1),
        'rc_rating': rc,
        'rc_trend': 0.0,
    })
    return profile


def test_4x4_analysis_math_is_small_and_complete():
    own = ("24890", "24889", "23782", "21773")
    opp = ("o1", "o2", "o3", "o4")
    profiles = {pid: _profile(40, 80) for pid in (*own, *opp)}
    matchups = {}
    schedule = ((3, 1), (0, 2), (2, 3), (1, 0), (0, 1), (3, 2), (2, 0), (1, 3), (0, 0), (1, 1), (2, 2), (3, 3))

    results = []
    for own_order in permutations(own):
        expected = 0.0
        for opp_order in permutations(opp):
            singles = [_matchup_probability(own_order[h], opp_order[a], profiles, matchups, own_is_home=True) for h, a in schedule]
            doubles = [
                _pair_probability(own_order[0], own_order[1], opp_order[0], opp_order[1], profiles),
                _pair_probability(own_order[2], own_order[3], opp_order[2], opp_order[3], profiles),
            ]
            game_probs = singles[:4] + doubles[:1] + singles[4:8] + doubles[1:] + singles[8:]
            win, draw, loss = _team_result_probabilities(game_probs)
            expected += win / 24.0
        results.append(expected)

    assert len(results) == 24
    assert all(0.0 <= value <= 1.0 for value in results)
    assert max(results) - min(results) < 1e-12
