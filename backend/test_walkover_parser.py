import unittest

from app.xttv_db_import import _is_valid_4_player_report, _is_valid_importable_report, _is_valid_walkover_report
from app.xttv_import import fetch_match
from app.xttv_parser import parse_match


class TestWalkoverParser(unittest.TestCase):
    MISSING_421 = [437859, 437877, 437880, 437916, 437928]

    def test_missing_421_walkover_reports_parse_and_validate(self):
        for meid in self.MISSING_421:
            with self.subTest(meid=meid):
                html, _, _, _ = fetch_match(meid)
                parsed = parse_match(html, meid)
                self.assertTrue(parsed.get("has_walkover"))
                self.assertFalse(_is_valid_4_player_report(parsed))
                self.assertTrue(_is_valid_walkover_report(parsed))
                self.assertTrue(_is_valid_importable_report(parsed))
                self.assertGreaterEqual(parsed["player_count"], 4)
                self.assertGreaterEqual(parsed["singles_count"], 4)

    def test_reference_full_report_still_valid(self):
        html, _, _, _ = fetch_match(437757)
        parsed = parse_match(html, 437757)
        self.assertFalse(parsed.get("has_walkover"))
        self.assertTrue(_is_valid_4_player_report(parsed))
        self.assertTrue(_is_valid_importable_report(parsed))


if __name__ == "__main__":
    unittest.main()
