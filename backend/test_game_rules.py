import unittest

from app.xttv_parser import _expected_singles_count


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


if __name__ == "__main__":
    unittest.main()
