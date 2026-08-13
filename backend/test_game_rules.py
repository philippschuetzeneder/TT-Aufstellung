import unittest

from app.xttv_parser import _expected_singles_count, _validate_game_structure


class TestGameRules(unittest.TestCase):
    def test_valid_results_and_expected_single_counts(self):
        expected = {
            (10, 0): 8,
            (9, 1): 8,
            (8, 2): 8,
            (8, 3): 9,
            (8, 4): 10,
            (8, 5): 11,
            (8, 6): 12,
            (7, 7): 12,
        }
        for score, singles in expected.items():
            with self.subTest(score=score):
                self.assertEqual(_expected_singles_count(*score), singles)

    def test_impossible_results_are_rejected(self):
        for score in ((10, 1), (10, 2), (9, 2), (8, 0), (8, 1), (8, 7), (7, 8)):
            with self.subTest(score=score):
                with self.assertRaises(ValueError):
                    _expected_singles_count(*score)

    def test_game_structure_accepts_8_2(self):
        games = [
            {"sequence": i, "game_type": "singles", "winner_side": "home" if i <= 8 else "away"}
            for i in (1, 2, 3, 4, 6, 7, 8, 9)
        ]
        # 6 singles home + 2 singles away, then both doubles home = 8:2.
        games[6]["winner_side"] = "away"
        games[7]["winner_side"] = "away"
        games += [
            {"sequence": 5, "game_type": "doubles", "winner_side": "home"},
            {"sequence": 10, "game_type": "doubles", "winner_side": "home"},
        ]
        _validate_game_structure(games, 8, 2)

    def test_game_structure_accepts_10_0(self):
        games = [
            {"sequence": i, "game_type": "singles", "winner_side": "home"}
            for i in (1, 2, 3, 4, 6, 7, 8, 9)
        ]
        games += [
            {"sequence": 5, "game_type": "doubles", "winner_side": "home"},
            {"sequence": 10, "game_type": "doubles", "winner_side": "home"},
        ]
        _validate_game_structure(games, 10, 0)

    def test_game_structure_rejects_wrong_single_count(self):
        games = [
            {"sequence": i, "game_type": "singles", "winner_side": "home" if i < 8 else "away"}
            for i in range(1, 10)
        ]
        games += [
            {"sequence": 5, "game_type": "doubles", "winner_side": "home"},
            {"sequence": 10, "game_type": "doubles", "winner_side": "away"},
        ]
        with self.assertRaises(ValueError):
            _validate_game_structure(games, 8, 2)

    def test_game_structure_requires_doubles_5_and_10(self):
        games = [
            {"sequence": i, "game_type": "singles", "winner_side": "home" if i <= 8 else "away"}
            for i in (1, 2, 3, 4, 6, 7, 8, 9)
        ]
        games += [
            {"sequence": 5, "game_type": "doubles", "winner_side": "home"},
            {"sequence": 11, "game_type": "doubles", "winner_side": "home"},
        ]
        with self.assertRaises(ValueError):
            _validate_game_structure(games, 8, 2)

    def test_game_structure_rejects_duplicate_sequences(self):
        games = [
            {"sequence": i, "game_type": "singles", "winner_side": "home" if i <= 8 else "away"}
            for i in (1, 2, 3, 4, 6, 7, 8, 9)
        ]
        games += [
            {"sequence": 5, "game_type": "doubles", "winner_side": "home"},
            {"sequence": 5, "game_type": "doubles", "winner_side": "away"},
        ]
        with self.assertRaises(ValueError):
            _validate_game_structure(games, 8, 2)


if __name__ == "__main__":
    unittest.main()
