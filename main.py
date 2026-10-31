
import os
import re
import html
import asyncio
import subprocess
import time
from pathlib import Path

import feedparser
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
from google import genai

# ==================================================
# DIGITAL ORBIT - USA NEWS SHORTS
# Banner Background + Large Subtitles
# ==================================================

RSS_URL = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"
MODEL = "gemini-3.8-flash"

VIDEO_DIR = Path("output_videos")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = Path("scripts.txt")
BANNER_FILE = Path("digital_orbit_banner.png")
LOGO_FILE = Path("digital_orbit_logo.png")

MAX_STORIES = 3
WIDTH = 1080
HEIGHT = 1920

VOICE = "en-US-AndrewNeural"

API_KEY_NAMES = [
    "GOOGLE_API_KEY_1",
    "GOOGLE_API_KEY_2",
    "GOOGLE_API_KEY_3",
    "GOOGLE_API_KEY"
]

EXHAUSTED_KEYS = set()


# ==================================================
# API KEYS
# ==================================================

def get_api_keys():
    keys = []

    for name in API_KEY_NAMES:
        value = os.environ.get(name, "").strip()

        if value and value not in keys:
            keys.append(value)

    if not keys:
        raise RuntimeError("Google API keys missing.")

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


# ==================================================
# GEMINI SCRIPT GENERATION
# ==================================================

def generate_script(keys, prompt):

    for slot, key in enumerate(keys, 1):

        if key in EXHAUSTED_KEYS:
            print("Key", slot, "already exhausted.")
            continue

        for attempt in range(2):

            try:
                client = genai.Client(api_key=key)

                response = client.models.generate_content(
                    model=MODEL,
                    contents=prompt
                )

                script = (response.text or "").strip()

                if script:
                    print("Script generated using key", slot)
                    return script

            except Exception as error:

                code = error_code(error)

                print(
                    "API error:",
                    code,
                    str(error)[:200]
                )

                if code == 429:
                    EXHAUSTED_KEYS.add(key)
                    print("Quota exhausted. Trying next key.")
                    break

                if code in (500, 503) and attempt == 0:
                    print("Temporary server error. Waiting 8 seconds.")
                    time.sleep(8)
                    continue

                break

    print("All available API keys failed.")
    return ""


# ==================================================
# FONT
# ==================================================

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
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


# ==================================================
# LOAD LOCAL IMAGE
# ==================================================

def load_local_image(path):

    try:
        if path.exists():
            return Image.open(path).convert("RGB")

    except Exception as error:
        print("Image warning:", str(error)[:150])

    return None


# ==================================================
# BANNER BACKGROUND
# ==================================================

def create_background(headline, index):

    banner = load_local_image(BANNER_FILE)

    if banner is not None:

        # Use the existing DIGITAL ORBIT banner
        # as the complete video background.

        background = ImageOps.fit(
            banner,
            (WIDTH, HEIGHT),
            method=Image.Resampling.LANCZOS
        )

        background = background.filter(
            ImageFilter.GaussianBlur(radius=5)
        )

        dark_layer = Image.new(
            "RGB",
            (WIDTH, HEIGHT),
            (2, 10, 28)
        )

        background = Image.blend(
            background,
            dark_layer,
            0.62
        )

        print("Existing DIGITAL ORBIT banner loaded.")

    else:

        print(
            "WARNING: digital_orbit_banner.png not found."
        )

        background = Image.new(
            "RGB",
            (WIDTH, HEIGHT),
            (4, 13, 32)
        )

    # Transparent dark panels
    overlay = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0)
    )

    od = ImageDraw.Draw(overlay)

    # Header
    od.rectangle(
        (0, 0, WIDTH, 275),
        fill=(2, 10, 28, 205)
    )

    # Subtitle panel
    od.rounded_rectangle(
        (38, 390, WIDTH - 38, 1500),
        radius=38,
        fill=(3, 16, 39, 218),
        outline=(35, 150, 225, 255),
        width=4
    )

    # Footer
    od.rectangle(
        (0, 1620, WIDTH, HEIGHT),
        fill=(2, 8, 22, 220)
    )

    od.rectangle(
        (0, 1620, WIDTH, 1635),
        fill=(215, 30, 48, 255)
    )

    background = Image.alpha_composite(
        background.convert("RGBA"),
        overlay
    ).convert("RGB")

    draw = ImageDraw.Draw(background)

    # Optional logo
    logo = load_local_image(LOGO_FILE)

    if logo is not None:

        logo.thumbnail(
            (145, 145),
            Image.Resampling.LANCZOS
        )

        background.paste(logo, (45, 45))
        draw = ImageDraw.Draw(background)

    title_x = 220 if logo is not None else 55

    draw.text(
        (title_x, 55),
        "DIGITAL ORBIT",
        font=get_font(58, True),
        fill="white"
    )

    draw.text(
        (title_x, 135),
        "USA NEWS | NEWS BRIEFING",
        font=get_font(32, True),
        fill=(45, 210, 255)
    )

    draw.rounded_rectangle(
        (65, 215, 420, 285),
        radius=12,
        fill=(195, 25, 45)
    )

    draw.text(
        (88, 231),
        "LATEST UPDATE",
        font=get_font(31, True),
        fill="white"
    )

    # Headline
    draw.text(
        (WIDTH // 2, 470),
        "NEWS HEADLINE",
        font=get_font(38, True),
        fill=(255, 205, 65),
        anchor="mm"
    )

    headline_font = get_font(39, True)

    words = headline.upper().split()
    lines = []
    current = ""

    for word in words:

        trial = (current + " " + word).strip()

        width = draw.textbbox(
            (0, 0),
            trial,
            font=headline_font
        )[2]

        if width <= WIDTH - 150:
            current = trial

        else:
            if current:
                lines.append(current)

            current = word

    if current:
        lines.append(current)

    y = 535

    for line in lines[:3]:

        draw.text(
            (WIDTH // 2, y),
            line,
            font=headline_font,
            fill="white",
            anchor="mt"
        )

        y += 58

    draw.line(
        (110, 750, WIDTH - 110, 750),
        fill=(45, 160, 220),
        width=3
    )

    draw.text(
        (WIDTH // 2, 820),
        "LIVE NEWS CAPTIONS",
        font=get_font(34, True),
        fill=(45, 210, 255),
        anchor="mm"
    )

    # Footer
    draw.rectangle(
        (0, 1660, WIDTH, 1760),
        fill=(185, 25, 43)
    )

    draw.text(
        (55, 1688),
        "USA NEWS",
        font=get_font(42, True),
        fill="white"
    )

    draw.text(
        (55, 1810),
        "DIGITAL ORBIT",
        font=get_font(34, True),
        fill=(45, 210, 255)
    )

    draw.text(
        (55, 1860),
        "STAY INFORMED | NEWS BRIEFING",
        font=get_font(24, True),
        fill=(230, 238, 250)
    )

    output = VIDEO_DIR / f"background_{index:02}.png"

    background.save(output)

    return output


# ==================================================
# VOICE GENERATION
# ==================================================

async def create_voice(script, path):

    communicator = edge_tts.Communicate(
        script,
        VOICE,
        rate="-5%"
    )

    await communicator.save(str(path))


# ==================================================
# SUBTITLE TIMESTAMP
# ==================================================

def timestamp(seconds):

    millis = max(0, int(seconds * 1000))

    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)

    return (
        f"{hours:02}:"
        f"{minutes:02}:"
        f"{secs:02},"
        f"{millis:03}"
    )


# ==================================================
# CREATE SUBTITLES
# ==================================================

def create_srt(script, duration, path):

    words = script.split()

    if not words:
        return False

    # Four words per subtitle screen
    chunks = [
        words[i:i + 4]
        for i in range(0, len(words), 4)
    ]

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as file:

        for i, chunk in enumerate(chunks):

            start = duration * i / len(chunks)
            end = duration * (i + 1) / len(chunks)

            file.write(f"{i + 1}\n")

            file.write(
                f"{timestamp(start)} --> "
                f"{timestamp(end)}\n"
            )

            file.write(
                " ".join(chunk) + "\n\n"
            )

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
        timeout=300
    )


def filter_path(path):

    return (
        path.resolve().as_posix()
        .replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


# ==================================================
# MAKE VIDEO
# ==================================================

def make_video(headline, script, index):

    audio_path = VIDEO_DIR / f"voice_{index:02}.mp3"
    srt_path = VIDEO_DIR / f"captions_{index:02}.srt"
    video_path = VIDEO_DIR / f"news_{index:02}.mp4"
    ticker_path = VIDEO_DIR / f"ticker_{index:02}.txt"

    # Banner background only.
    # No RSS image or AI image.

    background_path = create_background(
        headline,
        index
    )

    asyncio.run(
        create_voice(
            script,
            audio_path
        )
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
        text=True,
        timeout=30
    )

    duration = float(probe.stdout.strip())

    if duration < 20 or duration > 65:

        print(
            "Video skipped: invalid duration:",
            duration
        )

        return False

    if not create_srt(
        script,
        duration,
        srt_path
    ):
        return False

    ticker_path.write_text(
        "LATEST USA NEWS | DIGITAL ORBIT | STAY INFORMED",
        encoding="utf-8"
    )

    srt = filter_path(srt_path)
    ticker = filter_path(ticker_path)

    # Large subtitles in the middle of the banner.
    subtitles_filter = (
        f"subtitles=filename='{srt}':"
        "force_style='"
        "FontName=DejaVu Sans,"
        "FontSize=43,"
        "Bold=1,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00101828,"
        "BorderStyle=1,"
        "Outline=3,"
        "Shadow=1,"
        "Alignment=5,"
        "MarginV=0,"
        "MarginL=90,"
        "MarginR=90'"
    )

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
        f"{subtitles_filter},{ticker_filter}"
    )

    run_command([
        "ffmpeg",
        "-y",
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
        str(video_path)
    ])

    if (
        not video_path.exists()
        or video_path.stat().st_size < 100000
    ):
        raise RuntimeError("Video validation failed.")

    print("VIDEO CREATED:", video_path)

    return True


# ==================================================
# MAIN PROGRAM
# ==================================================

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
                command[0] + " is not installed."
            )

    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        raise RuntimeError("No RSS news received.")

    scripts = []
    seen = set()
    video_count = 0
    attempted = 0

    def save_scripts():

        content = "\n\n---\n\n".join(scripts)

        OUTPUT_FILE.write_text(
            content,
            encoding="utf-8"
        )

        (VIDEO_DIR / "scripts.txt").write_text(
            content,
            encoding="utf-8"
        )

        print("Scripts saved.")

    for entry in feed.entries:

        if video_count >= MAX_STORIES or attempted >= 8:
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
                    r"<[^>]+>",
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
        attempted += 1

        if len(summary) < 40:

            print(
                "Story skipped: insufficient summary:",
                headline
            )

            continue

        prompt = f"""
Write an original 30–60 second English news script
for a general US audience.

Target length: 90 to 130 words.

Use only the supplied headline and summary.
Do not invent facts, quotes, dates or sources.
Do not claim independent verification.

If information is insufficient, reply exactly:
SKIP_STORY

HEADLINE:
{headline}

RSS SUMMARY:
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

            print("Story skipped:", headline)
            continue

        scripts.append(
            f"HEADLINE: {headline}\n"
            f"SOURCE: {link}\n\n"
            f"{script}"
        )

        # Save immediately before video rendering.
        save_scripts()

        print("SCRIPT CREATED:", headline)

        try:

            if make_video(
                headline,
                script,
                len(scripts)
            ):
                video_count += 1

        except Exception as error:

            print(
                "VIDEO FAILED:",
                headline,
                str(error)[:500]
            )

    save_scripts()

    print("================================")
    print("FINISHED")
    print("Videos created:", video_count)
    print("Scripts saved:", len(scripts))
    print("================================")


if __name__ == "__main__":
    main()
