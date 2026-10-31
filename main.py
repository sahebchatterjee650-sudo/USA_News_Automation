import os
import re
import html
import asyncio
import subprocess
import time
import base64
from pathlib import Path
from io import BytesIO
from html.parser import HTMLParser
from urllib.parse import urlparse

import feedparser
import requests
import edge_tts

from PIL import Image, ImageDraw, ImageFont, ImageOps
from google import genai

# =====================================================
# DIGITAL ORBIT - USA NEWS AUTOMATION
# =====================================================

RSS_URL = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"

TEXT_MODEL = "gemini-3.8-flash"
IMAGE_MODEL = "gemini-nano-banana-2.1"

VOICES = [
    "en-US-AndrewNeural",  # Male
    "en-US-JennyNeural"    # Female
]

MAX_STORIES = 3

WIDTH = 1080
HEIGHT = 1920

VIDEO_DIR = Path("output_videos")
VIDEO_DIR.mkdir(exist_ok=True)

LOGO_FILE = Path("digital_orbit_logo.png")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 DigitalOrbitNews/1.0"
})


# =====================================================
# API KEYS
# =====================================================

def get_api_keys():

    names = [
        "GOOGLE_API_KEY_1",
        "GOOGLE_API_KEY_2",
        "GOOGLE_API_KEY_3",
        "GOOGLE_API_KEY"
    ]

    keys = []

    for name in names:

        value = os.environ.get(name, "").strip()

        if value and value not in keys:
            keys.append(value)

    if not keys:
        raise RuntimeError("Google API key missing.")

    return keys


# =====================================================
# AI NEWS SCRIPT
# =====================================================

def generate_script(keys, prompt):

    for attempt in range(3):

        for slot, key in enumerate(keys, 1):

            try:

                client = genai.Client(api_key=key)

                response = client.models.generate_content(
                    model=TEXT_MODEL,
                    contents=prompt
                )

                result = (response.text or "").strip()

                if result:
                    print("Script generated using key", slot)
                    return result

            except Exception as error:

                print(
                    "Script API error:",
                    str(error)[:250]
                )

        if attempt < 2:
            time.sleep(5 * (attempt + 1))

    return ""


# =====================================================
# HTML IMAGE PARSER
# No regex quotation error
# =====================================================

class ImageParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):

        if tag.lower() == "img":

            for name, value in attrs:

                if name.lower() == "src" and value:
                    self.urls.append(value)


# =====================================================
# DOWNLOAD ORIGINAL NEWS IMAGE
# =====================================================

def get_news_image(entry):

    candidates = []

    for group in (
        "media_content",
        "media_thumbnail",
        "enclosures"
    ):

        for item in entry.get(group, []) or []:

            url = item.get("url") or item.get("href")

            if url:
                candidates.append(url)

    raw = (
        entry.get("summary")
        or entry.get("description")
        or ""
    )

    parser = ImageParser()
    parser.feed(raw)

    candidates.extend(parser.urls)

    for url in dict.fromkeys(candidates):

        try:

            if urlparse(url).scheme not in ("http", "https"):
                continue

            response = session.get(
                url,
                timeout=15
            )

            response.raise_for_status()

            content_type = response.headers.get(
                "Content-Type", ""
            )

            if not content_type.startswith("image/"):
                continue

            if len(response.content) > 12 * 1024 * 1024:
                continue

            image = Image.open(
                BytesIO(response.content)
            )

            image = ImageOps.exif_transpose(
                image
            ).convert("RGB")

            if image.width >= 250 and image.height >= 150:

                print("Original news image downloaded.")
                return image

        except Exception as error:

            print(
                "Image download skipped:",
                str(error)[:100]
            )

    print("Original news image not found.")

    return None


# =====================================================
# AI IMAGE GENERATION
# =====================================================

def generate_ai_image(keys, headline, summary, number):

    prompt = f"""
Create a realistic editorial news photograph
for a United States news video.

NEWS HEADLINE:
{headline}

NEWS CONTEXT:
{summary[:1200]}

Create a visually relevant scene based on this news.

Requirements:
- Professional news photography.
- Realistic documentary style.
- Vertical 9:16 composition.
- Natural lighting.
- Clear main subject.
- High quality.
- No text.
- No captions.
- No logos.
- No watermarks.
- No fabricated charts.
- Do not present an illustrative scene as proof
  that an unverified event actually happened.

This is an editorial illustration for a news report.
"""

    for slot, key in enumerate(keys, 1):

        try:

            client = genai.Client(api_key=key)

            result = client.interactions.create(
                model=IMAGE_MODEL,
                input=prompt,
                response_format={
                    "type": "image",
                    "mime_type": "image/jpeg",
                    "aspect_ratio": "9:16",
                    "image_size": "1K"
                }
            )

            if not result.output_image:
                raise RuntimeError("No image returned.")

            image_data = base64.b64decode(
                result.output_image.data
            )

            image = Image.open(
                BytesIO(image_data)
            ).convert("RGB")

            image_path = (
                VIDEO_DIR /
                f"ai_news_image_{number:02}.jpg"
            )

            image.save(
                image_path,
                quality=92
            )

            print(
                "AI NEWS IMAGE CREATED:",
                image_path
            )

            return image

        except Exception as error:

            print(
                f"AI image attempt {slot} failed:",
                str(error)[:250]
            )

    print("AI image unavailable.")

    return None


# =====================================================
# FONT
# =====================================================

def get_font(size, bold=False):

    if bold:

        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        ]

    else:

        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
        ]

    for path in paths:

        if Path(path).exists():

            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


# =====================================================
# TEXT WRAPPING
# =====================================================

def wrap_text(draw, text, font, max_width):

    lines = []
    current = ""

    for word in text.split():

        test = (current + " " + word).strip()

        width = draw.textbbox(
            (0, 0),
            test,
            font=font
        )[2]

        if width <= max_width:

            current = test

        else:

            if current:
                lines.append(current)

            current = word

    if current:
        lines.append(current)

    return lines


# =====================================================
# VIDEO BACKGROUND
# =====================================================

def create_background(headline, image, number):

    canvas = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (4, 13, 32)
    )

    draw = ImageDraw.Draw(canvas)

    # Header

    draw.rectangle(
        (0, 0, WIDTH, 240),
        fill=(3, 15, 38)
    )

    draw.text(
        (45, 45),
        "DIGITAL ORBIT",
        font=get_font(58, True),
        fill="white"
    )

    draw.text(
        (50, 130),
        "USA NEWS | LATEST UPDATE",
        font=get_font(30, True),
        fill=(50, 210, 255)
    )

    # Main news image

    image_x = 30
    image_y = 265
    image_w = WIDTH - 60
    image_h = 780

    if image is not None:

        featured = ImageOps.fit(
            image,
            (image_w, image_h),
            method=Image.Resampling.LANCZOS
        )

    else:

        # Designed fallback, not repeated channel banner

        featured = Image.new(
            "RGB",
            (image_w, image_h),
            (12, 35, 75)
        )

        fd = ImageDraw.Draw(featured)

        fd.text(
            (45, 60),
            "NEWS VISUAL",
            font=get_font(42, True),
            fill=(50, 210, 255)
        )

        for i, line in enumerate(
            wrap_text(
                fd,
                headline,
                get_font(55, True),
                image_w - 90
            )[:8]
        ):

            fd.text(
                (45, 170 + i * 78),
                line,
                font=get_font(55, True),
                fill="white"
            )

    canvas.paste(
        featured,
        (image_x, image_y)
    )

    draw = ImageDraw.Draw(canvas)

    draw.rectangle(
        (30, 1070, WIDTH - 30, 1150),
        fill=(200, 25, 45)
    )

    draw.text(
        (55, 1088),
        "LATEST NEWS",
        font=get_font(38, True),
        fill="white"
    )

    # Headline

    draw.rounded_rectangle(
        (30, 1180, WIDTH - 30, 1450),
        radius=20,
        fill=(7, 25, 55),
        outline=(30, 150, 230),
        width=3
    )

    headline_font = get_font(43, True)

    for i, line in enumerate(
        wrap_text(
            draw,
            headline.upper(),
            headline_font,
            WIDTH - 130
        )[:4]
    ):

        draw.text(
            (65, 1215 + i * 58),
            line,
            font=headline_font,
            fill="white"
        )

    # Subtitle panel

    draw.rectangle(
        (30, 1480, WIDTH - 30, 1630),
        fill=(3, 12, 29)
    )

    draw.text(
        (55, 1490),
        "NEWS BRIEFING",
        font=get_font(25, True),
        fill=(50, 210, 255)
    )

    # Bottom ticker

    draw.rectangle(
        (0, 1650, WIDTH, HEIGHT),
        fill=(2, 9, 23)
    )

    draw.rectangle(
        (0, 1650, WIDTH, 1665),
        fill=(220, 30, 45)
    )

    draw.rectangle(
        (0, 1690, WIDTH, 1770),
        fill=(190, 25, 43)
    )

    draw.text(
        (45, 1708),
        "USA NEWS",
        font=get_font(38, True),
        fill="white"
    )

    draw.text(
        (45, 1810),
        "DIGITAL ORBIT",
        font=get_font(32, True),
        fill=(50, 210, 255)
    )

    draw.text(
        (45, 1860),
        "STAY INFORMED | NEWS BRIEFING",
        font=get_font(24, True),
        fill="white"
    )

    output = (
        VIDEO_DIR /
        f"background_{number:02}.png"
    )

    canvas.save(output)

    return output


# =====================================================
# VOICE + WORD TIMINGS
# =====================================================

async def create_voice_and_timings(text, audio_path, voice):

    communicator = edge_tts.Communicate(
        text,
        voice,
        rate="-5%"
    )

    audio_data = bytearray()
    word_events = []

    async for event in communicator.stream():

        if event["type"] == "audio":

            audio_data.extend(
                event["data"]
            )

        elif event["type"] == "WordBoundary":

            word_events.append({
                "text": event["text"],
                "start": event["offset"] / 10000000,
                "duration": event["duration"] / 10000000
            })

    if not audio_data:
        raise RuntimeError("Voice audio was not generated.")

    audio_path.write_bytes(audio_data)

    if not word_events:
        raise RuntimeError("TTS word timings were not returned.")

    return word_events


# =====================================================
# SRT SUBTITLES
# =====================================================

def srt_timestamp(seconds):

    milliseconds = max(
        0,
        int(seconds * 1000)
    )

    hours, milliseconds = divmod(
        milliseconds,
        3600000
    )

    minutes, milliseconds = divmod(
        milliseconds,
        60000
    )

    seconds, milliseconds = divmod(
        milliseconds,
        1000
    )

    return (
        f"{hours:02}:"
        f"{minutes:02}:"
        f"{seconds:02},"
        f"{milliseconds:03}"
    )


def create_subtitles(word_events, path):

    chunks = []

    for i in range(0, len(word_events), 4):

        group = word_events[i:i + 4]

        start = group[0]["start"]

        end = (
            group[-1]["start"]
            + group[-1]["duration"]
        )

        words = " ".join(
            item["text"]
            for item in group
        )

        chunks.append(
            (start, end, words)
        )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as file:

        for number, (start, end, words) in enumerate(
            chunks,
            1
        ):

            file.write(
                f"{number}\n"
            )

            file.write(
                f"{srt_timestamp(start)} --> "
                f"{srt_timestamp(end)}\n"
            )

            file.write(
                words + "\n\n"
            )

    if not chunks:
        raise RuntimeError("Subtitle file is empty.")

    print("SUBTITLES CREATED:", path)


# =====================================================
# VIDEO CREATION
# =====================================================

def create_video(headline, summary, script, entry, number, keys):

    audio_path = (
        VIDEO_DIR /
        f"voice_{number:02}.mp3"
    )

    srt_path = (
        VIDEO_DIR /
        f"captions_{number:02}.srt"
    )

    video_path = (
        VIDEO_DIR /
        f"news_{number:02}.mp4"
    )

    # First original image, then AI image

    news_image = get_news_image(entry)

    if news_image is None:

        news_image = generate_ai_image(
            keys,
            headline,
            summary,
            number
        )

    background_path = create_background(
        headline,
        news_image,
        number
    )

    voice = VOICES[
        (number - 1) % len(VOICES)
    ]

    print("Using voice:", voice)

    word_events = asyncio.run(
        create_voice_and_timings(
            script,
            audio_path,
            voice
        )
    )

    create_subtitles(
        word_events,
        srt_path
    )

    probe = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ],
        check=True,
        capture_output=True,
        text=True
    )

    duration = float(
        probe.stdout.strip()
    )

    if not 20 <= duration <= 65:

        raise RuntimeError(
            f"Audio duration invalid: {duration:.1f}"
        )

    # FFmpeg subtitle path escaping

    subtitle_file = (
        srt_path.resolve()
        .as_posix()
        .replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )

    subtitle_filter = (
        f"subtitles=filename='{subtitle_file}':"
        "force_style='"
        "FontName=DejaVu Sans,"
        "FontSize=38,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=3,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=320,"
        "MarginL=55,"
        "MarginR=55'"
    )

    video_filter = (
        "zoompan="
        "z='min(zoom+0.00025,1.035)':"
        "d=1:"
        "s=1080x1920:"
        "fps=25,"
        + subtitle_filter
    )

    command = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-framerate", "25",
        "-i", str(background_path),
        "-i", str(audio_path),
        "-vf", video_filter,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-r", "25",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", str(duration),
        "-shortest",
        "-movflags", "+faststart",
        str(video_path)
    ]

    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=300
    )

    if (
        not video_path.exists()
        or video_path.stat().st_size < 100000
    ):

        raise RuntimeError(
            "Video validation failed."
        )

    print(
        "VIDEO CREATED:",
        video_path
    )


# =====================================================
# MAIN PROGRAM
# =====================================================

def main():

    keys = get_api_keys()

    for command in (
        ["ffmpeg", "-version"],
        ["ffprobe", "-version"]
    ):

        if subprocess.run(
            command,
            capture_output=True
        ).returncode != 0:

            raise RuntimeError(
                command[0] + " is missing."
            )

    feed = feedparser.parse(RSS_URL)

    if not feed.entries:

        raise RuntimeError(
            "RSS returned no news."
        )

    scripts = []
    seen = set()
    video_count = 0

    for entry in feed.entries:

        if video_count >= MAX_STORIES:
            break

        headline = (
            entry.get("title") or ""
        ).strip()

        link = (
            entry.get("link") or ""
        ).strip()

        raw_summary = (
            entry.get("summary")
            or entry.get("description")
            or ""
        )

        summary = re.sub(
            r"\s+",
            " ",
            html.unescape(
                re.sub(
                    r"<[^>]*>",
                    " ",
                    raw_summary
                )
            )
        ).strip()

        if (
            not headline
            or not link
            or link in seen
        ):
            continue

        seen.add(link)

        prompt = f"""
Write an original English news narration
for a United States audience.

Length: 90 to 130 words.
Video duration target: 30 to 60 seconds.

Use only facts from the headline and summary.
Never invent facts, dates, quotes or sources.
Do not claim independent verification.
If information is insufficient, reply exactly:
SKIP_STORY

HEADLINE:
{headline}

SUMMARY:
{summary}

SOURCE:
{link}
"""

        script = generate_script(
            keys,
            prompt
        )

        if (
            not script
            or "SKIP_STORY" in script.upper()
            or len(script.split()) < 65
        ):

            print(
                "Story skipped:",
                headline
            )

            continue

        print(
            "SCRIPT CREATED:",
            headline
        )

        try:

            create_video(
                headline,
                summary,
                script,
                entry,
                video_count + 1,
                keys
            )

            scripts.append(
                f"HEADLINE: {headline}\n"
                f"SOURCE: {link}\n\n"
                f"{script}"
            )

            video_count += 1

        except Exception as error:

            print(
                "VIDEO FAILED:",
                headline,
                str(error)[:500]
            )

    Path("scripts.txt").write_text(
        "\n\n---\n\n".join(scripts),
        encoding="utf-8"
    )

    print(
        f"FINISHED: {video_count} videos created."
    )


if __name__ == "__main__":

    main()
