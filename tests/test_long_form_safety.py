import unittest
from pathlib import Path

class LongFormSafetyTests(unittest.TestCase):
    def test_private_only_upload_has_no_scheduling(self):
        s=Path("long_video_upload.py").read_text(encoding="utf-8")
        self.assertIn('"privacyStatus":"private"',s)
        self.assertNotIn('"publishAt"',s)
    def test_separate_from_shorts(self):
        w=Path(".github/workflows/long-video.yml").read_text(encoding="utf-8")
        shorts=Path(".github/workflows/news.yml").read_text(encoding="utf-8")
        self.assertIn("long_form.py",w)
        self.assertIn("python -u main.py",shorts)
        self.assertNotIn("long_form.py",shorts)
    def test_landscape_duration_and_word_guards(self):
        s=Path("long_form.py").read_text(encoding="utf-8")
        self.assertIn("16/9",s)
        self.assertIn("240<=duration<=360",s)
        self.assertIn("MIN_WORDS,MAX_WORDS=520,730",s)

if __name__=="__main__": unittest.main()
