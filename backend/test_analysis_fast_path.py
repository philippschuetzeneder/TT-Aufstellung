from itertools import permutations

from app.analysis_service import _pair_probability, _team_win_probability, _matchup_probability


def test_4x4_analysis_math_is_small_and_complete():
    own = ("24890", "24889", "23782", "21773")
    opp = ("o1", "o2", "o3", "o4")
    stats = {pid: (40, 80) for pid in (*own, *opp)}
    matchups = {}
    schedule = ((0,0),(1,1),(2,2),(3,3),(0,1),(1,0),(2,3),(3,2),(0,2),(2,0),(1,3),(3,1))

    results = []
    for own_order in permutations(own):
        expected = 0.0
        for opp_order in permutations(opp):
            singles = [_matchup_probability(own_order[h], opp_order[a], stats, matchups) for h, a in schedule]
            doubles = [
                _pair_probability(own_order[0], own_order[1], opp_order[0], opp_order[1], stats),
                _pair_probability(own_order[2], own_order[3], opp_order[2], opp_order[3], stats),
            ]
            expected += _team_win_probability(singles + doubles) / 24.0
        results.append(expected)

    assert len(results) == 24
    assert all(0.0 <= value <= 1.0 for value in results)
    assert max(results) - min(results) < 1e-12
