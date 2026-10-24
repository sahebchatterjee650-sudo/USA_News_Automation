
import os
import re
import html
import asyncio
import subprocess
import textwrap
from pathlib import Path
from io import BytesIO
from urllib.parse import urlparse

import feedparser
import requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from google import genai

# --------------------------------------------------
# DIGITAL ORBIT USA NEWS AUTOMATION
# --------------------------------------------------

RSS_URL = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"
OUTPUT_FILE = "scripts.txt"
VIDEO_DIR = Path("output_videos")

LOGO_FILE = Path("digital_orbit_logo.png")
BANNER_FILE = Path("digital_orbit_banner.png")

MODEL = "gemini-3.8-flash"
VOICE = "en-US-AndrewNeural"

MAX_STORIES = 3
WIDTH, HEIGHT = 1080, 1920

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "DigitalOrbitNewsBot/1.0"
})


# --------------------------------------------------
# API KEYS
# --------------------------------------------------

def get_api_keys():
    names = [
        "GOOGLE_API_KEY_1",
        "GOOGLE_API_KEY_2",
        "GOOGLE_API_KEY_3",
        "GOOGLE_API_KEY",
    ]

    keys = []
    for name in names:
        key = os.environ.get(name, "").strip()
        if key and key not in keys:
            keys.append(key)

    if not keys:
        raise RuntimeError("No Google API keys configured.")

    return keys


def generate_script(keys, prompt):
    last_error = ""

    for index, key in enumerate(keys, start=1):
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )

            script = (response.text or "").strip()

            if script:
                print(f"Script generated using key slot {index}.")
                return script

        except Exception as exc:
            last_error = str(exc)
            lower = last_error.lower()

            retryable = any(term in lower for term in [
                "429",
                "resource_exhausted",
                "quota exceeded",
                "rate limit",
                "503",
                "unavailable",
                "server disconnected",
                "deadline exceeded",
            ])

            if retryable:
                print(
                    f"Key slot {index} unavailable; "
                    "trying the next configured key."
                )
                continue

            print(
                f"Key slot {index} failed: {last_error[:250]}"
            )
            break

    print("AI generation failed:", last_error[:300])
    return ""


# --------------------------------------------------
# FONTS AND IMAGE HELPERS
# --------------------------------------------------

def get_font(size, bold=False):
    candidates = []

    if bold:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ])
    else:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ])

    for filename in candidates:
        if Path(filename).exists():
            return ImageFont.truetype(filename, size)

    return ImageFont.load_default()


def load_image(path):
    try:
        if path.exists():
            return Image.open(path).convert("RGB")
    except Exception as exc:
        print("Image load warning:", str(exc)[:200])

    return None


def fit_cover(image, size):
    return ImageOps.fit(
        image.convert("RGB"),
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def draw_wrapped_text(draw, text, x, y, max_width,
                      font, fill, line_gap=12, max_lines=4):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        trial = f"{current} {word}".strip()

        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
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


# --------------------------------------------------
# GET STORY IMAGE FROM RSS
# --------------------------------------------------

def get_story_image(entry):
    candidates = []

    for media in entry.get("media_content", []) or []:
        if media.get("url"):
            candidates.append(media["url"])

    for media in entry.get("media_thumbnail", []) or []:
        if media.get("url"):
            candidates.append(media["url"])

    for enclosure in entry.get("enclosures", []) or []:
        href = enclosure.get("href") or enclosure.get("url")
        mime = enclosure.get("type", "")

        if href and (
            mime.startswith("image/")
            or re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", href, re.I)
        ):
            candidates.append(href)

    # Some RSS summaries include an image inside HTML.
    for field in ("summary", "description"):
        raw = entry.get(field, "") or ""
        match = re.search(
            r'<img[^>]+src=["\']([^"\']+)["\']',
            raw,
            re.I,
        )
        if match:
            candidates.append(html.unescape(match.group(1)))

    for url in candidates:
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                continue

            response = SESSION.get(url, timeout=12)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                continue

            if len(response.content) > 12 * 1024 * 1024:
                continue

            image = Image.open(BytesIO(response.content))
            image = ImageOps.exif_transpose(image).convert("RGB")

            if image.width >= 250 and image.height >= 150:
                print("Story image downloaded.")
                return image

        except Exception:
            continue

    print("No suitable RSS image found; using branded background.")
    return None


# --------------------------------------------------
# CREATE NEWS BACKGROUND
# --------------------------------------------------

def create_background(headline, story_image, index):
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (5, 15, 38))
    draw = ImageDraw.Draw(canvas)

    banner = load_image(BANNER_FILE)
    logo = None

    try:
        if LOGO_FILE.exists():
            logo = Image.open(LOGO_FILE).convert("RGBA")
    except Exception:
        logo = None

    # Header background uses the uploaded banner when available.
    if banner:
        header = fit_cover(banner, (WIDTH, 360))
        header = header.filter(ImageFilter.GaussianBlur(1))
        canvas.paste(header, (0, 0))
        draw.rectangle((0, 0, WIDTH, 360), fill=(2, 12, 32, 90))
    else:
        draw.rectangle((0, 0, WIDTH, 360), fill=(4, 28, 66))

    # Darken header slightly so branding remains readable.
    overlay = Image.new("RGBA", (WIDTH, 360), (0, 8, 25, 100))
    canvas.paste(
        Image.alpha_composite(
            canvas.crop((0, 0, WIDTH, 360)).convert("RGBA"),
            overlay,
        ).convert("RGB"),
        (0, 0),
    )

    draw = ImageDraw.Draw(canvas)

    if logo:
        logo.thumbnail((190, 190), Image.Resampling.LANCZOS)
        x = 38
        y = 30
        canvas_rgba = canvas.convert("RGBA")
        canvas_rgba.alpha_composite(logo, (x, y))
        canvas = canvas_rgba.convert("RGB")
        draw = ImageDraw.Draw(canvas)

    draw.text(
        (250, 65),
        "DIGITAL ORBIT",
        font=get_font(55, True),
        fill=(255, 255, 255),
        stroke_width=2,
        stroke_fill=(0, 20, 55),
    )

    draw.text(
        (255, 135),
        "USA NEWS",
        font=get_font(38, True),
        fill=(40, 220, 255),
    )

    draw.rounded_rectangle(
        (255, 205, 600, 265),
        radius=14,
        fill=(210, 30, 48),
    )
    draw.text(
        (278, 216),
        "LATEST UPDATE",
        font=get_font(27, True),
        fill="white",
    )

    # Main story image.
    image_top = 385
    image_height = 720
    image_box = (35, image_top, WIDTH - 35, image_top + image_height)

    if story_image:
        featured = fit_cover(
            story_image,
            (image_box[2] - image_box[0],
             image_box[3] - image_box[1]),
        )
    elif banner:
        featured = fit_cover(banner, (
            image_box[2] - image_box[0],
            image_box[3] - image_box[1],
        ))
    else:
        featured = Image.new(
            "RGB",
            (image_box[2] - image_box[0],
             image_box[3] - image_box[1]),
            (12, 38, 78),
        )

    canvas.paste(featured, (image_box[0], image_box[1]))
    draw = ImageDraw.Draw(canvas)

    # Image border and lower gradient-style dark strip.
    draw.rectangle(image_box, outline=(30, 190, 255), width=5)
    draw.rectangle(
        (35, 1020, WIDTH - 35, 1105),
        fill=(2, 12, 30),
    )
    draw.text(
        (60, 1040),
        "DIGITAL ORBIT  |  USA NEWS",
        font=get_font(30, True),
        fill=(255, 205, 70),
    )

    # Breaking-news label.
    draw.rectangle((35, 1140, WIDTH - 35, 1220), fill=(205, 25, 45))
    draw.text(
        (60, 1155),
        "BREAKING NEWS",
        font=get_font(39, True),
        fill="white",
    )

    # Headline.
    draw.rounded_rectangle(
        (35, 1250, WIDTH - 35, 1575),
        radius=18,
        fill=(7, 24, 55),
        outline=(15, 130, 230),
        width=3,
    )

    draw_wrapped_text(
        draw,
        headline.upper(),
        65,
        1290,
        WIDTH - 130,
        get_font(47, True),
        "white",
        line_gap=13,
        max_lines=5,
    )

    # Caption-safe region. Actual subtitles are overlaid by FFmpeg.
    draw.rectangle(
        (35, 1595, WIDTH - 35, 1745),
        fill=(4, 13, 31),
    )
    draw.text(
        (60, 1635),
        "STAY INFORMED  •  DIGITAL ORBIT",
        font=get_font(27, True),
        fill=(55, 215, 255),
    )

    # Bottom news ticker base. The scrolling text is added in FFmpeg.
    draw.rectangle((0, 1775, WIDTH, HEIGHT), fill=(3, 10, 25))
    draw.rectangle((0, 1775, WIDTH, 1787), fill=(235, 35, 50))
    draw.rectangle((0, 1787, WIDTH, 1795), fill=(35, 190, 255))

    draw.text(
        (35, 1820),
        "USA NEWS",
        font=get_font(34, True),
        fill=(255, 205, 65),
    )

    draw.text(
        (35, 1880),
        "DIGITAL ORBIT  •  NEWS BRIEFING",
        font=get_font(23, True),
        fill=(230, 240, 255),
    )

    output_path = VIDEO_DIR / f"background_{index:02}.png"
    canvas.save(output_path, quality=95)
    return output_path


# --------------------------------------------------
# VOICE AND CAPTIONS
# --------------------------------------------------

async def create_voice(script, path):
    speech = edge_tts.Communicate(
        script,
        VOICE,
        rate="-5%",
    )
    await speech.save(str(path))


def timestamp(seconds):
    millis = max(0, int(seconds * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    seconds_part, millis = divmod(millis, 1000)

    return (
        f"{hours:02}:{minutes:02}:"
        f"{seconds_part:02},{millis:03}"
    )


def create_srt(script, duration, path):
    words = script.split()
    if not words:
        return False

    chunks = [
        words[i:i + 6]
        for i in range(0, len(words), 6)
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


# --------------------------------------------------
# FFMPEG VIDEO RENDERING
# --------------------------------------------------

def run_command(command):
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=240,
    )


def make_video(headline, script, entry, index):
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

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

    asyncio.run(create_voice(script, audio_path))

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
        print("VIDEO FAILED: voice duration outside 20–65 seconds.")
        return False

    create_srt(script, duration, srt_path)

    ticker_path.write_text(
        "LATEST USA NEWS   |   DIGITAL ORBIT   |   STAY INFORMED",
        encoding="utf-8",
    )

    # The ticker scrolls from right to left.
    subtitle_path = srt_path.as_posix().replace("\\", "/")
    ticker_file = ticker_path.as_posix().replace("\\", "/")

    subtitle_filter = (
        f"subtitles=filename='{subtitle_path}':"
        "force_style='FontName=DejaVu Sans,"
        "FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,"
        "Outline=2,Shadow=1,Alignment=2,"
        "MarginV=235,MarginL=65,MarginR=65'"
    )

    ticker_filter = (
        "drawtext="
        f"textfile='{ticker_file}':"
        "fontcolor=white:fontsize=30:"
        "x=w-mod(t*180\\,w+tw):"
        "y=h-100:"
        "box=1:boxcolor=0x06132B@0.96:"
        "boxborderw=12"
    )

    video_filter = f"{subtitle_filter},{ticker_filter}"

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


# --------------------------------------------------
# MAIN AUTOMATION
# --------------------------------------------------

def main():
    keys = get_api_keys()
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    if subprocess.run(
        ["ffmpeg", "-version"],
        capture_output=True,
    ).returncode != 0:
        raise RuntimeError("FFmpeg is not installed.")

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
        summary = re.sub(
            r"<[^>]+>",
            " ",
            html.unescape(entry.get("summary", "") or ""),
        ).strip()

        if not headline or not link or link in seen:
            continue

        seen.add(link)

        prompt = f"""
Write an original 30–60 second English YouTube Shorts news script
for a general US audience, aiming for 90–130 words.

Use only the information in the headline and summary.
Do not invent facts, quotes, dates, or claim independent verification.
If information is insufficient, reply exactly SKIP_STORY.

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
