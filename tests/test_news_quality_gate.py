import tempfile
import unittest
from pathlib import Path

from news_quality_gate import classify_category, trend_relevance, is_near_duplicate, validate_rendered_assets


class NewsQualityGateTests(unittest.TestCase):
    def test_black_friday_is_business_not_politics(self):
        self.assertEqual(classify_category("These Black Friday deals are at their lowest prices ever"), "BUSINESS & ECONOMY")

    def test_thanksgiving_storm_is_weather(self):
        self.assertEqual(classify_category("Millions clean up after massive Thanksgiving storms on East Coast"), "WEATHER & CLIMATE")

    def test_unrelated_trend_is_not_matched_by_one_shared_place(self):
        self.assertEqual(trend_relevance("Texas midterm election results", "Texas tech basketball"), 0.0)

    def test_unrelated_trend_is_not_matched_without_shared_topic(self):
        self.assertEqual(trend_relevance("Thanksgiving storm cleanup across East Coast", "Mega Millions winning numbers"), 0.0)

    def test_related_trend_is_matched(self):
        self.assertGreater(trend_relevance("Thanksgiving storm cleanup and flooding", "Thanksgiving storm"), 0.0)

    def test_near_duplicate_titles_are_detected(self):
        self.assertTrue(is_near_duplicate("Massive Thanksgiving storm hits East Coast", "Thanksgiving storm hits East Coast"))

    def test_unrelated_titles_are_not_duplicates(self):
        self.assertFalse(is_near_duplicate("Black Friday shopping deals", "Senate election results"))

    def test_missing_rendered_asset_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ok, reason = validate_rendered_assets(root / "missing.mp4", root / "missing.jpg", root / "missing.ass")
            self.assertFalse(ok)
            self.assertIn("missing", reason)


if __name__ == "__main__":
    unittest.main()
