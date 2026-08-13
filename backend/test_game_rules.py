import pytest

from app.xttv_parser import _expected_singles_count


@pytest.mark.parametrize(
    ("home_wins", "away_wins", "expected_singles"),
    [
        (10, 0, 8),
        (9, 1, 8),
        (8, 2, 8),
        (8, 3, 9),
        (8, 4, 10),
        (8, 5, 11),
        (8, 6, 12),
        (7, 7, 12),
    ],
)
def test_valid_tt_match_results(home_wins, away_wins, expected_singles):
    assert _expected_singles_count(home_wins, away_wins) == expected_singles


@pytest.mark.parametrize(
    ("home_wins", "away_wins"),
    [
        (10, 1),
        (10, 2),
        (9, 2),
        (10, 0),  # sanity check is valid and covered above
    ],
)
def test_invalid_results_are_rejected_except_known_10_0(home_wins, away_wins):
    if (home_wins, away_wins) == (10, 0):
        assert _expected_singles_count(home_wins, away_wins) == 8
    else:
        with pytest.raises(ValueError):
            _expected_singles_count(home_wins, away_wins)
