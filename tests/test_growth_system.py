import time
import unittest

import main


class GrowthRankingTests(unittest.TestCase):
    """Offline checks only; these tests never call paid APIs or upload videos."""
    def test_growth_score_is_bounded(self):
        entry = {
            "title": "US Congress approves new tax bill for 2026",
            "_trend_score": 6,
            "published_parsed": time.gmtime(time.time() - 1800),
        }
        score = main.score_viral_opportunity(entry)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        self.assertIn("_growth_reasons", entry)

    def test_trend_matched_story_is_prioritized(self):
        entries = [
            {"title": "A local community event announced", "_trend_score": 0},
            {"title": "Congress debates new tax bill", "_trend_score": 6},
        ]
        ranked = main.rank_stories_for_growth(entries)
        self.assertEqual(ranked[0]["title"], "Congress debates new tax bill")

    def test_multiword_trends_reject_single_word_false_matches(self):
        entries = [
            {"title": "Russian forces hit Kyiv with ballistic missiles, mayor says"},
            {"title": "Talking Shop with Tyler Cameron"},
            {"title": "Amazon to launch Amazon Autos in UK in early 2027"},
        ]
        topics = ["watson hit", "cameron diaz", "superbowl 2027"]
        ranked = main.rank_stories_by_trend_interest(entries, topics)
        self.assertTrue(all(item.get("_trend_score", 0) == 0 for item in ranked))
        self.assertTrue(all(item.get("_trend_match", "") == "" for item in ranked))

    def test_fast_clear_reliable_accepts_recent_traceable_story(self):
        entry = {"published_parsed": time.gmtime(time.time() - 3600)}
        headline = "US officials announce a major public safety update"
        summary = (
            "Federal officials announced a new public safety update on Tuesday. "
            "The agency said the measure will apply across several states and "
            "provided details about the timing, affected communities, and the "
            "steps residents should take. Officials said additional information "
            "will be published through the agency website as implementation begins."
        )
        self.assertTrue(
            main.passes_fast_clear_reliable_gate(
                entry, headline, "https://example.gov/news/update", summary
            )
        )

    def test_fast_clear_reliable_rejects_stale_story(self):
        entry = {"published_parsed": time.gmtime(time.time() - 72 * 3600)}
        summary = " ".join(["This is a sufficiently detailed source summary."] * 9)
        self.assertFalse(
            main.passes_fast_clear_reliable_gate(
                entry,
                "US officials announce a major public safety update",
                "https://example.gov/news/update",
                summary,
            )
        )


if __name__ == "__main__":
    unittest.main()
