import unittest
from PIL import Image, ImageDraw
import main


class ShortsThumbnailLayoutTests(unittest.TestCase):
    def test_long_headline_tokens_never_exceed_safe_width(self):
        image = Image.new("RGB", (1080, 1920), "black")
        draw = ImageDraw.Draw(image)
        fnt = main.get_font(42, True)
        lines = main.draw_wrapped_text(
            draw,
            "A VERY LONG INTERNATIONAL ORGANIZATION NAME WITH AN EXTRAORDINARILYLONGUNBROKENWORD",
            fnt,
            870,
            4,
        )
        self.assertLessEqual(len(lines), 4)
        self.assertTrue(all(draw.textbbox((0, 0), line, font=fnt)[2] <= 870 for line in lines))

    def test_empty_headline_is_safe(self):
        image = Image.new("RGB", (1080, 1920), "black")
        draw = ImageDraw.Draw(image)
        self.assertEqual(main.draw_wrapped_text(draw, "", main.get_font(42, True), 870), [])


if __name__ == "__main__":
    unittest.main()
