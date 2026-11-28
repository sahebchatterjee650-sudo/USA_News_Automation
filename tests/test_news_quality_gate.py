import tempfile
import unittest
from pathlib import Path

from news_quality_gate import classify_category, trend_relevance, is_near_duplicate, validate_rendered_assets, split_visual_segments, visual_scene_label


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


    def test_script_is_split_into_five_visual_beats(self):
        script = (
            "Officials announced the new plan today. The measure follows months of debate. "
            "Local agencies will begin the first phase next week. Residents will receive updates "
            "as more details become available."
        )
        segments = split_visual_segments(script, 5)
        self.assertEqual(len(segments), 5)
        self.assertTrue(all(segments))

    def test_original_narration_rejects_verbatim_source_copy(self):
        from news_quality_gate import validate_original_narration
        source = (
            "Officials announced a new transit plan on Monday. The plan will add "
            "service on three routes next month. The agency said riders can expect "
            "shorter waits during peak travel hours. Public comments remain open."
        )
        copied = (
            "Officials announced a new transit plan on Monday. The plan will add "
            "service on three routes next month. The agency said riders can expect "
            "shorter waits during peak travel hours. Public comments remain open."
        )
        ok, reason = validate_original_narration(copied, source, min_words=20)
        self.assertFalse(ok)
        self.assertIn("publisher wording", reason)

    def test_original_narration_accepts_paraphrased_explainer(self):
        from news_quality_gate import validate_original_narration
        source = (
            "Officials announced a new transit plan on Monday. The plan will add "
            "service on three routes next month. The agency said riders can expect "
            "shorter waits during peak travel hours. Public comments remain open."
        )
        script = (
            "A change to the city's commute is now on the calendar. Officials say "
            "three bus routes will receive more service starting next month. That "
            "could mean less time waiting during the busiest travel periods. The "
            "agency is still collecting public feedback before the plan moves ahead."
        )
        ok, reason = validate_original_narration(script, source)
        self.assertTrue(ok, reason)

    def test_visual_scene_cards_are_five_distinct_labels(self):
        labels = [visual_scene_label(i) for i in range(1, 6)]
        self.assertEqual(len(set(labels)), 5)

    def test_missing_rendered_asset_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ok, reason = validate_rendered_assets(root / "missing.mp4", root / "missing.jpg", root / "missing.ass")
            self.assertFalse(ok)
            self.assertIn("missing", reason)


if __name__ == "__main__":
    unittest.main()
