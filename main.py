
import os
import re
import html
import asyncio
import subprocess
import time
import random
from pathlib import Path
from io import BytesIO
from urllib.parse import urlparse

import feedparser
import requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
from google import genai

# ==================================================
# DIGITAL ORBIT USA NEWS SHORTS
# ==================================================

RSS_URL = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"

VIDEO_DIR = Path("output_videos")
OUTPUT_FILE = "scripts.txt"

LOGO_FILE = Path("digital_orbit_logo.png")
BANNER_FILE = Path("digital_orbit_banner.png")

MODEL = "gemini-3.8-flash"
VOICES = ["en-US-AndrewNeural", "en-US-JennyNeural"]  # male, female, male, repeat

MAX_STORIES = 3
WIDTH, HEIGHT = 1080, 1920

VIDEO_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 DigitalOrbitNews/1.0"
})


# ==================================================
# API KEYS: GitHub Repository Secrets
# ==================================================

def get_api_keys():
    names = [
        "GOOGLE_API_KEY_1",
        "GOOGLE_API_KEY_2",
        "GOOGLE_API_KEY_3",
        "GOOGLE_API_KEY",
    ]

    keys = []

    for name in names:
        value = os.environ.get(name, "").strip()
        if value and value not in keys:
            keys.append(value)

    if not keys:
        raise RuntimeError("No Google API keys configured.")

    return keys


def generate_script(keys, prompt):
    last_error = ""
    for attempt in range(3):
        for slot, key in enumerate(keys, start=1):
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=MODEL,
                    contents=prompt,
                )
                script = (response.text or "").strip()
                if script:
                    print(f"Script generated using key slot {slot}.")
                    return script
                last_error = "Empty response from model."
            except Exception as exc:
                last_error = str(exc)
                low = last_error.lower()
                retryable = any(term in low for term in (
                    "429", "503", "unavailable", "resource_exhausted",
                    "quota exceeded", "rate limit", "server disconnected",
                    "deadline exceeded", "temporarily",
                ))
                print(
                    f"AI error on key {slot}, attempt "
                    f"{attempt + 1}/3: {last_error[:180]}"
                )
                if not retryable:
                    return ""
        if attempt < 2:
            delay = (2 ** attempt) * 5
            print(f"Retrying AI generation in {delay}s.")
            time.sleep(delay)
    print("AI generation failed after retries:", last_error[:300])
    return ""


# ==================================================
# FONTS AND IMAGE HELPERS
# ==================================================

def get_font(size, bold=False):
    if bold:
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
    else:
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]

    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def load_local_image(path):
    try:
        if path.exists():
            return Image.open(path).convert("RGB")
    except Exception as exc:
        print("Local image warning:", str(exc)[:150])

    return None


def cover_image(image, size):
    return ImageOps.fit(
        image.convert("RGB"),
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def draw_wrapped(draw, text, x, y, max_width, font,
                 fill, max_lines=4, line_gap=10):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        trial = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), trial, font=font)[2]

        if width <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    lines = lines[:max_lines]

    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += font.size + line_gap

    return y


# ==================================================
# DOWNLOAD AN IMAGE FROM THE RSS ENTRY
# ==================================================

def get_story_image(entry):
    candidates = []

    for item in entry.get("media_content", []) or []:
        url = item.get("url")
        if url:
            candidates.append(url)

    for item in entry.get("media_thumbnail", []) or []:
        url = item.get("url")
        if url:
            candidates.append(url)

    for item in entry.get("enclosures", []) or []:
        url = item.get("href") or item.get("url")
        mime = item.get("type", "")

        if url and (
            mime.startswith("image/")
            or re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url, re.I)
        ):
            candidates.append(url)

    for field in ("summary", "description"):
        raw = entry.get(field, "") or ""
        match = re.search(
            r'<img[^>]+src=["\']([^"\']+)["\']',
            raw,
            re.I,
        )

        if match:
            candidates.append(html.unescape(match.group(1)))

    for url in dict.fromkeys(candidates):
        try:
            if urlparse(url).scheme not in ("http", "https"):
                continue

            response = session.get(url, timeout=12)
            response.raise_for_status()

            if not response.headers.get(
                "Content-Type", ""
            ).startswith("image/"):
                continue

            if len(response.content) > 12 * 1024 * 1024:
                continue

            image = Image.open(BytesIO(response.content))
            image = ImageOps.exif_transpose(image).convert("RGB")

            if image.width >= 250 and image.height >= 150:
                print("News image found.")
                return image

        except Exception:
            continue

    print("No RSS image available; using the branded fallback.")
    return None


# ==================================================
# CREATE THE TV NEWS LAYOUT
# ==================================================

def create_background(headline, story_image, index):
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (4, 13, 32))
    draw = ImageDraw.Draw(canvas)

    banner = load_local_image(BANNER_FILE)
    logo = None

    try:
        if LOGO_FILE.exists():
            logo = Image.open(LOGO_FILE).convert("RGBA")
    except Exception:
        pass

    # Header: use the uploaded banner as a subtle backdrop.
    if banner:
        header = cover_image(banner, (WIDTH, 290))
        header = header.filter(ImageFilter.GaussianBlur(2))
        canvas.paste(header, (0, 0))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, WIDTH, 290), fill=(2, 12, 32))

    if banner:
        header = cover_image(banner, (WIDTH, 290))
        header = Image.blend(
            header,
            Image.new("RGB", header.size, (2, 12, 32)),
            0.42,
        )
        canvas.paste(header, (0, 0))

    draw = ImageDraw.Draw(canvas)

    # Logo on the left.
    if logo:
        logo.thumbnail((170, 170), Image.Resampling.LANCZOS)
        canvas_rgba = canvas.convert("RGBA")
        canvas_rgba.alpha_composite(logo, (35, 35))
        canvas = canvas_rgba.convert("RGB")
        draw = ImageDraw.Draw(canvas)

    draw.text(
        (225, 55),
        "DIGITAL ORBIT",
        font=get_font(49, True),
        fill="white",
    )

    draw.text(
        (228, 120),
        "USA NEWS",
        font=get_font(34, True),
        fill=(45, 210, 255),
    )

    draw.rounded_rectangle(
        (225, 180, 525, 238),
        radius=10,
        fill=(205, 27, 45),
    )
    draw.text(
        (244, 192),
        "LATEST UPDATE",
        font=get_font(25, True),
        fill="white",
    )

    # Main image area.
    image_x = 35
    image_y = 315
    image_w = WIDTH - 70
    image_h = 640

    if story_image is not None:
        featured = cover_image(story_image, (image_w, image_h))
    elif banner is not None:
        featured = cover_image(banner, (image_w, image_h))
    else:
        featured = Image.new(
            "RGB", (image_w, image_h), (12, 35, 75)
        )

    canvas.paste(featured, (image_x, image_y))
    draw = ImageDraw.Draw(canvas)

    draw.rectangle(
        (image_x, image_y, image_x + image_w, image_y + image_h),
        outline=(30, 185, 255),
        width=5,
    )

    draw.rectangle((35, 975, WIDTH - 35, 1045), fill=(5, 18, 42))
    draw.text(
        (55, 994),
        "DIGITAL ORBIT  |  USA NEWS",
        font=get_font(29, True),
        fill=(255, 205, 65),
    )

    # Breaking-news banner.
    draw.rectangle((35, 1070, WIDTH - 35, 1145), fill=(205, 28, 45))
    draw.text(
        (58, 1086),
        "LATEST NEWS",
        font=get_font(37, True),
        fill="white",
    )

    # Headline panel: text is kept inside a fixed area.
    panel = (35, 1170, WIDTH - 35, 1390)

    draw.rounded_rectangle(
        panel,
        radius=18,
        fill=(7, 24, 55),
        outline=(20, 145, 240),
        width=3,
    )

    draw_wrapped(
        draw,
        headline.upper(),
        62,
        1200,
        WIDTH - 124,
        get_font(39, True),
        "white",
        max_lines=4,
        line_gap=8,
    )

    # Dedicated subtitle area. FFmpeg subtitles are placed here.
    draw.rectangle((35, 1415, WIDTH - 35, 1610), fill=(3, 12, 29))

    draw.text(
        (58, 1430),
        "NEWS BRIEFING",
        font=get_font(25, True),
        fill=(40, 210, 255),
    )

    draw.rounded_rectangle(
        (55, 1480, WIDTH - 55, 1585),
        radius=12,
        fill=(12, 27, 48),
        outline=(45, 90, 135),
        width=2,
    )

    # Ticker band.
    draw.rectangle((0, 1630, WIDTH, HEIGHT), fill=(2, 9, 23))
    draw.rectangle((0, 1630, WIDTH, 1642), fill=(225, 30, 48))
    draw.rectangle((0, 1642, WIDTH, 1650), fill=(30, 190, 255))

    draw.rectangle((0, 1665, WIDTH, 1735), fill=(190, 25, 43))
    draw.text(
        (35, 1682),
        "USA NEWS",
        font=get_font(34, True),
        fill="white",
    )

    draw.text(
        (35, 1770),
        "DIGITAL ORBIT",
        font=get_font(30, True),
        fill=(45, 210, 255),
    )

    draw.text(
        (35, 1820),
        "STAY INFORMED  |  NEWS BRIEFING",
        font=get_font(24, True),
        fill=(230, 238, 250),
    )

    output = VIDEO_DIR / f"background_{index:02}.png"
    canvas.save(output)
    return output


# ==================================================
# VOICE GENERATION
# ==================================================

async def create_voice(script, path, voice):
    communicator = edge_tts.Communicate(
        script,
        voice,
        rate="-5%",
    )
    await communicator.save(str(path))


# ==================================================
# CAPTIONS WITH EXPLICIT BOTTOM POSITIONING
# ==================================================

def timestamp(seconds):
    millis = max(0, int(seconds * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)

    return (
        f"{hours:02}:{minutes:02}:"
        f"{secs:02},{millis:03}"
    )


def create_srt(script, duration, path):
    words = script.split()

    if not words:
        return False

    chunks = [
        words[i:i + 5]
        for i in range(0, len(words), 5)
    ]

    with open(path, "w", encoding="utf-8") as file:
        for i, chunk in enumerate(chunks):
            start = duration * i / len(chunks)
            end = duration * (i + 1) / len(chunks)

            file.write(f"{i + 1}\n")
            file.write(
                f"{timestamp(start)} --> {timestamp(end)}\n"
            )
            file.write(" ".join(chunk) + "\n\n")

    return True


# ==================================================
# FFMPEG HELPERS
# ==================================================

def run_command(command):
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=240,
    )


def filter_path(path):
    # Escape characters used by FFmpeg filter syntax.
    return (
        path.resolve().as_posix()
        .replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def make_video(headline, script, entry, index):
    audio_path = VIDEO_DIR / f"voice_{index:02}.mp3"
    srt_path = VIDEO_DIR / f"captions_{index:02}.srt"
    video_path = VIDEO_DIR / f"news_{index:02}.mp4"
    ticker_path = VIDEO_DIR / f"ticker_{index:02}.txt"

    story_image = get_story_image(entry)

    background_path = create_background(
        headline,
        story_image,
        index,
    )

    voice = VOICES[(index - 1) % len(VOICES)]
    print(f"Using voice for video {index}: {voice}")
    asyncio.run(create_voice(script, audio_path, voice))

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    duration = float(probe.stdout.strip())

    if duration < 20 or duration > 65:
        print("Video skipped: voice duration outside 20–65 seconds.")
        return False

    if not create_srt(script, duration, srt_path):
        return False

    ticker_path.write_text(
        "LATEST USA NEWS   |   DIGITAL ORBIT   |   STAY INFORMED",
        encoding="utf-8",
    )

    srt = filter_path(srt_path)
    ticker = filter_path(ticker_path)

    # Place subtitles in the dedicated lower caption panel.
    subtitles_filter = (
        f"subtitles=filename='{srt}':"
        "force_style='FontName=DejaVu Sans,"
        "FontSize=30,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=350,"
        "MarginL=65,"
        "MarginR=65'"
    )

    # Scroll ticker horizontally in the red ticker band.
    ticker_filter = (
        "drawtext="
        f"textfile='{ticker}':"
        "fontcolor=white:"
        "fontsize=27:"
        "x=w-mod(t*150\\,w+tw):"
        "y=1700:"
        "box=0"
    )

    video_filter = (
        "zoompan=z='min(zoom+0.00025,1.035)':d=1:"
        "s=1080x1920:fps=25,"
        f"{subtitles_filter},{ticker_filter}"
    )

    run_command([
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", "25",
        "-i", str(background_path),
        "-i", str(audio_path),
        "-vf", video_filter,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-r", "25",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", str(duration),
        "-shortest",
        "-movflags", "+faststart",
        str(video_path),
    ])

    print("VIDEO CREATED:", video_path)
    return True


# ==================================================
# MAIN
# ==================================================

def main():
    keys = get_api_keys()

    if subprocess.run(
        ["ffmpeg", "-version"],
        capture_output=True,
    ).returncode != 0:
        raise RuntimeError("FFmpeg is not installed.")

    if subprocess.run(
        ["ffprobe", "-version"],
        capture_output=True,
    ).returncode != 0:
        raise RuntimeError("FFprobe is not installed.")

    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        raise RuntimeError("No RSS news received.")

    scripts = []
    seen = set()
    video_count = 0

    for entry in feed.entries:
        if len(scripts) >= MAX_STORIES:
            break

        headline = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()

        raw_summary = (
            entry.get("summary")
            or entry.get("description")
            or ""
        )

        summary = re.sub(
            r"\s+",
            " ",
            html.unescape(re.sub(r"<[^>]+>", " ", raw_summary)),
        ).strip()

        if not headline or not link or link in seen:
            continue

        seen.add(link)

        prompt = f"""
Write an original 30–60 second English news script
for a general US audience, aiming for 90–130 words.

Use only the supplied headline and summary.
Do not invent facts, quotes, dates, or claim independent verification.
Do not add details absent from the source.
If the information is insufficient, reply exactly SKIP_STORY.

HEADLINE: {headline}
RSS SUMMARY: {summary}
SOURCE URL: {link}
"""

        script = generate_script(keys, prompt)

        if (
            not script
            or "SKIP_STORY" in script.upper()
            or len(script.split()) < 65
        ):
            print("Story skipped:", headline)
            continue

        scripts.append(
            f"HEADLINE: {headline}\n"
            f"SOURCE: {link}\n\n"
            f"{script}\n"
        )

        print("SCRIPT CREATED:", headline)

        try:
            if make_video(headline, script, entry, len(scripts)):
                video_count += 1
        except Exception as exc:
            print("VIDEO FAILED:", headline, str(exc)[:500])

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        file.write("\n---\n\n".join(scripts))

    print(f"Saved {len(scripts)} scripts to {OUTPUT_FILE}")
    print(f"Created {video_count} videos.")

    if not scripts:
        print("No scripts were created.")


if __name__ == "__main__":
    main()
