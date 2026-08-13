import unittest
from datetime import date

from app.opponent_prediction_service import _recency_weight


class OpponentPredictionTests(unittest.TestCase):
    def test_recency_weight_is_one_for_reference_date(self):
        class Match:
            match_date = "2025-01-10"

        self.assertAlmostEqual(_recency_weight(Match(), date(2025, 1, 10)), 1.0)

    def test_recency_weight_halves_after_half_life(self):
        class Match:
            match_date = "2025-01-10"

        self.assertAlmostEqual(_recency_weight(Match(), date(2025, 2, 24)), 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
