
import os
import re
import html
import time
import asyncio
import subprocess
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

MAX_STORIES = 3
WIDTH, HEIGHT = 1080, 1920

VOICES = [
    "en-US-AndrewNeural",
    "en-US-JennyNeural"
]

OUT = Path("output_videos")
OUT.mkdir(exist_ok=True)

SCRIPTS_FILE = OUT / "scripts.txt"
ROOT_SCRIPTS_FILE = Path("scripts.txt")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 DigitalOrbitNews/1.0"
})

# Keys that returned quota errors are not retried
# again during this run.
EXHAUSTED_KEYS = set()


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
        raise RuntimeError("Google API keys are missing.")

    print("API keys loaded:", len(keys))
    return keys


def error_code(error):
    message = str(error).upper()

    if "429" in message or "RESOURCE_EXHAUSTED" in message:
        return 429

    if "503" in message or "UNAVAILABLE" in message:
        return 503

    if "500" in message or "INTERNAL" in message:
        return 500

    return 0


# =====================================================
# SAVE SCRIPTS IMMEDIATELY
# =====================================================

def save_scripts(items):
    content = "\n\n========== NEWS STORY ==========\n\n".join(items)

    SCRIPTS_FILE.write_text(content, encoding="utf-8")
    ROOT_SCRIPTS_FILE.write_text(content, encoding="utf-8")

    print("Scripts saved:", SCRIPTS_FILE)


def add_script(items, headline, link, script):
    items.append(
        f"HEADLINE: {headline}\n"
        f"SOURCE: {link}\n\n"
        f"{script}"
    )
    save_scripts(items)


# =====================================================
# GEMINI SCRIPT GENERATION WITH KEY ROTATION
# =====================================================

def generate_script(keys, prompt):
    global EXHAUSTED_KEYS

    for slot, key in enumerate(keys, 1):
        if key in EXHAUSTED_KEYS:
            print(f"Key {slot} already exhausted. Skipping.")
            continue

        # A maximum of two attempts for temporary server errors.
        for attempt in range(2):
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

                print("Empty response from key", slot)
                break

            except Exception as error:
                code = error_code(error)

                print(
                    f"Script API error on key {slot}: "
                    f"HTTP {code} - {str(error)[:220]}"
                )

                if code == 429:
                    EXHAUSTED_KEYS.add(key)
                    print(
                        f"Key {slot} quota exhausted. "
                        "Moving to next key."
                    )
                    break

                if code in (503, 500):
                    if attempt == 0:
                        print("Temporary server error. Waiting 8 seconds.")
                        time.sleep(8)
                        continue

                # Other errors: move to next key.
                break

    print("All available API keys failed for this story.")
    return ""


# =====================================================
# HTML IMAGE PARSER
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
# ORIGINAL NEWS IMAGE
# =====================================================

def get_news_image(entry):
    candidates = []

    for group in ("media_content", "media_thumbnail", "enclosures"):
        for item in entry.get(group, []) or []:
            url = item.get("url") or item.get("href")
            if url:
                candidates.append(url)

    raw = entry.get("summary") or entry.get("description") or ""
    parser = ImageParser()
    parser.feed(raw)
    candidates.extend(parser.urls)

    for url in dict.fromkeys(candidates):
        try:
            if urlparse(url).scheme not in ("http", "https"):
                continue

            response = session.get(url, timeout=15)
            response.raise_for_status()

            if not response.headers.get("Content-Type", "").startswith("image/"):
                continue

            if len(response.content) > 12 * 1024 * 1024:
                continue

            image = Image.open(BytesIO(response.content))
            image = ImageOps.exif_transpose(image).convert("RGB")

            if image.width >= 250 and image.height >= 150:
                print("Original news image downloaded.")
                return image

        except Exception as error:
            print("Image download skipped:", str(error)[:120])

    print("Original news image not found.")
    return None


# =====================================================
# AI IMAGE FALLBACK
# =====================================================

def generate_ai_image(keys, headline, summary, number):
    prompt = f"""
Create a realistic editorial illustration for a USA news video.

Headline: {headline}
Context: {summary[:1000]}

Vertical 9:16 documentary-style editorial image.
Professional news photography, natural lighting.
No text, no captions, no logos, no watermark.
Do not depict an unverified event as confirmed evidence.
"""

    for slot, key in enumerate(keys, 1):
        if key in EXHAUSTED_KEYS:
            continue

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

            data = base64.b64decode(result.output_image.data)
            image = Image.open(BytesIO(data)).convert("RGB")

            path = OUT / f"ai_news_image_{number:02}.jpg"
            image.save(path, quality=92)

            print("AI news image created:", path)
            return image

        except Exception as error:
            code = error_code(error)
            print(f"AI image error key {slot}: {code} {str(error)[:180]}")

            if code == 429:
                EXHAUSTED_KEYS.add(key)

    print("AI image unavailable. Using designed fallback.")
    return None


# =====================================================
# FONTS AND TEXT
# =====================================================

def get_font(size, bold=False):
    paths = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        ]
        if bold else
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
        ]
    )

    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    lines = []
    current = ""

    for word in text.split():
        test = (current + " " + word).strip()
        width = draw.textbbox((0, 0), test, font=font)[2]

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
# BACKGROUND DESIGN
# =====================================================

def create_background(headline, image, number):
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (4, 13, 32))
    draw = ImageDraw.Draw(canvas)

    draw.rectangle((0, 0, WIDTH, 240), fill=(3, 15, 38))
    draw.text((45, 45), "DIGITAL ORBIT", font=get_font(58, True), fill="white")
    draw.text(
        (50, 130), "USA NEWS | LATEST UPDATE",
        font=get_font(30, True), fill=(50, 210, 255)
    )

    image_w, image_h = WIDTH - 60, 780

    if image is not None:
        featured = ImageOps.fit(
            image, (image_w, image_h),
            method=Image.Resampling.LANCZOS
        )
    else:
        featured = Image.new("RGB", (image_w, image_h), (12, 35, 75))
        fd = ImageDraw.Draw(featured)
        fd.text((45, 60), "NEWS VISUAL", font=get_font(42, True), fill=(50, 210, 255))

        for i, line in enumerate(
            wrap_text(fd, headline, get_font(55, True), image_w - 90)[:8]
        ):
            fd.text((45, 170 + i * 78), line, font=get_font(55, True), fill="white")

    canvas.paste(featured, (30, 265))
    draw = ImageDraw.Draw(canvas)

    draw.rectangle((30, 1070, WIDTH - 30, 1150), fill=(200, 25, 45))
    draw.text((55, 1088), "LATEST NEWS", font=get_font(38, True), fill="white")

    draw.rounded_rectangle(
        (30, 1180, WIDTH - 30, 1450),
        radius=20, fill=(7, 25, 55),
        outline=(30, 150, 230), width=3
    )

    for i, line in enumerate(
        wrap_text(draw, headline.upper(), get_font(43, True), WIDTH - 130)[:4]
    ):
        draw.text((65, 1215 + i * 58), line, font=get_font(43, True), fill="white")

    draw.rectangle((30, 1480, WIDTH - 30, 1630), fill=(3, 12, 29))
    draw.text((55, 1490), "NEWS BRIEFING", font=get_font(25, True), fill=(50, 210, 255))

    draw.rectangle((0, 1650, WIDTH, HEIGHT), fill=(2, 9, 23))
    draw.rectangle((0, 1650, WIDTH, 1665), fill=(220, 30, 45))
    draw.rectangle((0, 1690, WIDTH, 1770), fill=(190, 25, 43))
    draw.text((45, 1708), "USA NEWS", font=get_font(38, True), fill="white")
    draw.text((45, 1810), "DIGITAL ORBIT", font=get_font(32, True), fill=(50, 210, 255))
    draw.text((45, 1860), "STAY INFORMED | NEWS BRIEFING", font=get_font(24, True), fill="white")

    path = OUT / f"background_{number:02}.png"
    canvas.save(path)
    return path


# =====================================================
# VOICE AND WORD TIMINGS
# =====================================================

async def create_voice_and_timings(text, audio_path, voice):
    communicator = edge_tts.Communicate(text, voice, rate="-5%")

    audio_data = bytearray()
    word_events = []

    async for event in communicator.stream():
        if event["type"] == "audio":
            audio_data.extend(event["data"])

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
# SUBTITLES
# =====================================================

def srt_timestamp(seconds):
    milliseconds = max(0, int(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)

    return (
        f"{hours:02}:{minutes:02}:"
        f"{seconds:02},{milliseconds:03}"
    )


def create_subtitles(word_events, path):
    chunks = []

    for i in range(0, len(word_events), 4):
        group = word_events[i:i + 4]
        start = group[0]["start"]
        end = group[-1]["start"] + group[-1]["duration"]
        words = " ".join(item["text"] for item in group)
        chunks.append((start, end, words))

    with open(path, "w", encoding="utf-8") as file:
        for number, (start, end, words) in enumerate(chunks, 1):
            file.write(
                f"{number}\n"
                f"{srt_timestamp(start)} --> {srt_timestamp(end)}\n"
                f"{words}\n\n"
            )

    if not chunks:
        raise RuntimeError("Subtitle file is empty.")

    print("Subtitles created:", path)


# =====================================================
# CREATE VIDEO
# =====================================================

def create_video(headline, summary, script, entry, number, keys):
    audio_path = OUT / f"voice_{number:02}.mp3"
    srt_path = OUT / f"captions_{number:02}.srt"
    video_path = OUT / f"news_{number:02}.mp4"

    news_image = get_news_image(entry)

    if news_image is None:
        news_image = generate_ai_image(keys, headline, summary, number)

    background = create_background(headline, news_image, number)

    voice = VOICES[(number - 1) % len(VOICES)]
    print("Using voice:", voice)

    word_events = asyncio.run(
        create_voice_and_timings(script, audio_path, voice)
    )

    create_subtitles(word_events, srt_path)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ],
        check=True, capture_output=True, text=True
    )

    duration = float(probe.stdout.strip())

    if not 20 <= duration <= 65:
        raise RuntimeError(f"Audio duration invalid: {duration:.1f} seconds")

    subtitle_file = (
        srt_path.resolve().as_posix()
        .replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )

    subtitle_filter = (
        f"subtitles=filename='{subtitle_file}':"
        "force_style='FontName=DejaVu Sans,"
        "FontSize=38,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,"
        "Outline=3,Shadow=1,Alignment=2,"
        "MarginV=320,MarginL=55,MarginR=55'"
    )

    video_filter = (
        "zoompan=z='min(zoom+0.00025,1.035)':"
        "d=1:s=1080x1920:fps=25,"
        + subtitle_filter
    )

    command = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", "25",
        "-i", str(background),
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

    if not video_path.exists() or video_path.stat().st_size < 100000:
        raise RuntimeError("Video validation failed.")

    print("VIDEO CREATED:", video_path)


# =====================================================
# MAIN
# =====================================================

def main():
    keys = get_api_keys()

    for command in (["ffmpeg", "-version"], ["ffprobe", "-version"]):
        if subprocess.run(command, capture_output=True).returncode != 0:
            raise RuntimeError(command[0] + " is missing.")

    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        raise RuntimeError("RSS returned no news.")

    scripts = []
    seen = set()
    video_count = 0
    attempted = 0

    # Keep any scripts from an earlier run in this workspace.
    if SCRIPTS_FILE.exists():
        old = SCRIPTS_FILE.read_text(encoding="utf-8").strip()
        if old:
            scripts.append(old)

    for entry in feed.entries:
        if video_count >= MAX_STORIES:
            break

        if attempted >= 8:
            print("Story attempt limit reached.")
            break

        headline = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()

        raw_summary = (
            entry.get("summary")
            or entry.get("description")
            or ""
        )

        summary = re.sub(
            r"\s+", " ",
            html.unescape(re.sub(r"<[^>]*>", " ", raw_summary))
        ).strip()

        if not headline or not link or link in seen:
            continue

        seen.add(link)
        attempted += 1

        if len(summary) < 40:
            print("Story skipped: insufficient summary:", headline)
            continue

        prompt = f"""
Write an original English news narration for a United States audience.

Length: 90 to 130 words.
Target video duration: 30 to 60 seconds.

Use only facts contained in the headline and summary.
Do not invent facts, dates, quotes, names or sources.
Do not claim independent verification.
If information is insufficient, reply exactly: SKIP_STORY

HEADLINE:
{headline}

SUMMARY:
{summary}

SOURCE:
{link}
"""

        script = generate_script(keys, prompt)

        if (
            not script
            or "SKIP_STORY" in script.upper()
            or len(script.split()) < 65
        ):
            print("Story skipped:", headline)
            continue

        # Save script BEFORE attempting video creation.
        add_script(scripts, headline, link, script)

        print("SCRIPT CREATED:", headline)

        try:
            create_video(
                headline,
                summary,
                script,
                entry,
                video_count + 1,
                keys
            )
            video_count += 1

        except Exception as error:
            print(
                "VIDEO FAILED:",
                headline,
                str(error)[:500]
            )
            # Script remains saved even when video fails.

    save_scripts(scripts)

    print(
        f"FINISHED: {video_count} videos created. "
        f"Scripts saved: {len(scripts)}"
    )


if __name__ == "__main__":
    main()
