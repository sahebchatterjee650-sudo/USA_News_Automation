import unittest

from growth_quality import classify_topic, discussion_prompt, build_hooks_and_titles


class GrowthQualityTests(unittest.TestCase):
    def test_zelensky_story_is_geopolitics_not_weather(self):
        self.assertEqual(classify_topic("Zelenskyy pushes back on Trump calling Putin a friend")[0], "geopolitics")

    def test_political_story_is_classified_as_politics(self):
        self.assertEqual(classify_topic("Advocacy group urges Democrats to build relationships with working class")[0], "politics")

    def test_iowa_munitions_blast_is_public_safety(self):
        self.assertEqual(classify_topic("Federal officials say zero airborne lead detected after Iowa munitions blast")[0], "public_safety")

    def test_judge_ruling_is_law(self):
        self.assertEqual(classify_topic("Judge declines to issue long-term ban on tax-funded ads")[0], "law")

    def test_discussion_prompt_is_topic_specific(self):
        self.assertIn("diplomatic", discussion_prompt("Zelenskyy pushes back on Trump calling Putin a friend"))

    def test_hooks_and_titles_are_headline_specific(self):
        hooks, titles = build_hooks_and_titles("Mexico files formal complaint to US over DEA tip line")
        self.assertIn("Mexico files formal complaint", hooks[0])
        self.assertTrue(all(len(title) > 15 for title in titles))
        self.assertFalse(any(title.endswith(" ") for title in titles))


if __name__ == "__main__":
    unittest.main()
