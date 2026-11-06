
import os
import re
import html
import time
import asyncio
import subprocess
from pathlib import Path

import feedparser
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
from google import genai

# =========================================================
# DIGITAL ORBIT - USA NEWS AUTOMATION
# 3 videos per batch | 9 videos per day
# Andrew + Jenny alternating voices
# =========================================================

RSS_URL = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"

# Keep the model configurable through GitHub Secrets/Variables
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

VIDEO_DIR = Path("output_videos")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = Path("scripts.txt")
BANNER_FILE = Path("digital_orbit_banner.png")
LOGO_FILE = Path("digital_orbit_logo.png")

MAX_STORIES = 3
MAX_ATTEMPTS = 12

WIDTH = 1080
HEIGHT = 1920

VOICES = [
    "en-US-AndrewNeural",
    "en-US-JennyNeural"
]

VOICE_OFFSET = int(os.getenv("VOICE_OFFSET", "0"))
BATCH_NUMBER = os.getenv("BATCH_NUMBER", "1")
ET_PUBLISH_TIME = os.getenv("ET_PUBLISH_TIME", "09:00")

API_KEY_NAMES = [
    "GOOGLE_API_KEY_1",
    "GOOGLE_API_KEY_2",
    "GOOGLE_API_KEY_3",
    "GOOGLE_API_KEY"
]

EXHAUSTED_KEYS = set()


# =========================================================
# API KEY MANAGEMENT
# =========================================================

def get_api_keys():
    keys = []

    for name in API_KEY_NAMES:
        value = os.getenv(name, "").strip()

        if value and value not in keys:
            keys.append(value)

    if not keys:
        raise RuntimeError("No Google API keys found.")

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


# =========================================================
# GEMINI SCRIPT GENERATION
# =========================================================

def generate_script(keys, prompt):

    for slot, key in enumerate(keys, 1):

        if key in EXHAUSTED_KEYS:
            continue

        client = genai.Client(api_key=key)

        for attempt in range(3):

            try:
                response = client.models.generate_content(
                    model=MODEL,
                    contents=prompt
                )

                script = (response.text or "").strip()

                if script:
                    print("Script generated with API key:", slot)
                    return script

                print("Empty Gemini response.")

            except Exception as error:

                code = error_code(error)

                print(
                    "Gemini error:",
                    code,
                    str(error)[:300]
                )

                if code == 429:
                    EXHAUSTED_KEYS.add(key)
                    print("Quota exhausted. Trying next API key.")
                    break

                if code in (500, 503):
                    wait_time = min(10 * (2 ** attempt), 60)
                    print("Temporary server error. Waiting:", wait_time)
                    time.sleep(wait_time)
                    continue

                # Do not retry permanent errors indefinitely.
                break

    print("All available API keys failed.")
    return ""


# =========================================================
# FONTS
# =========================================================

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


# =========================================================
# IMAGE LOADING
# =========================================================

def load_local_image(path):

    try:
        if path.exists():
            return Image.open(path).convert("RGB")

    except Exception as error:
        print("Image warning:", str(error)[:150])

    return None


# =========================================================
# BACKGROUND DESIGN
# =========================================================

def create_background(headline, index):

    banner = load_local_image(BANNER_FILE)

    if banner is not None:
        background = ImageOps.fit(
            banner,
            (WIDTH, HEIGHT),
            method=Image.Resampling.LANCZOS
        )

        background = background.filter(
            ImageFilter.GaussianBlur(radius=5)
        )

        dark = Image.new(
            "RGB",
            (WIDTH, HEIGHT),
            (2, 10, 28)
        )

        background = Image.blend(background, dark, 0.62)

        print("Digital Orbit banner loaded.")

    else:
        print("WARNING: Banner not found.")

        background = Image.new(
            "RGB",
            (WIDTH, HEIGHT),
            (4, 13, 32)
        )

    overlay = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0)
    )

    od = ImageDraw.Draw(overlay)

    od.rectangle(
        (0, 0, WIDTH, 275),
        fill=(2, 10, 28, 220)
    )

    od.rounded_rectangle(
        (38, 390, WIDTH - 38, 1500),
        radius=38,
        fill=(3, 16, 39, 225),
        outline=(35, 150, 225, 255),
        width=4
    )

    od.rectangle(
        (0, 1620, WIDTH, HEIGHT),
        fill=(2, 8, 22, 225)
    )

    background = Image.alpha_composite(
        background.convert("RGBA"),
        overlay
    ).convert("RGB")

    draw = ImageDraw.Draw(background)

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

    draw.text(
        (WIDTH // 2, 470),
        "NEWS HEADLINE",
        font=get_font(38, True),
        fill=(255, 205, 65),
        anchor="mm"
    )

    headline_font = get_font(39, True)
    lines = []
    current = ""

    for word in headline.upper().split():

        trial = (current + " " + word).strip()

        box = draw.textbbox(
            (0, 0),
            trial,
            font=headline_font
        )

        if box[2] <= WIDTH - 150:
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


# =========================================================
# VOICE GENERATION
# =========================================================

async def create_voice(script, path, voice):

    communicator = edge_tts.Communicate(
        script,
        voice,
        rate="-5%"
    )

    await communicator.save(str(path))


def get_voice(index):

    global_index = VOICE_OFFSET + index - 1
    return VOICES[global_index % len(VOICES)]


# =========================================================
# SUBTITLE TIME
# =========================================================

def ass_timestamp(seconds):

    centiseconds = max(0, int(seconds * 100))

    hours, centiseconds = divmod(centiseconds, 360000)
    minutes, centiseconds = divmod(centiseconds, 6000)
    secs, centiseconds = divmod(centiseconds, 100)

    return (
        f"{hours}:"
        f"{minutes:02}:"
        f"{secs:02}."
        f"{centiseconds:02}"
    )


# =========================================================
# CREATE SUBTITLES
# =========================================================

def create_ass(script, duration, path):

    words = script.split()

    if not words:
        return False

    # 4 words per caption.
    chunks = [
        words[i:i + 4]
        for i in range(0, len(words), 4)
    ]

    with open(path, "w", encoding="utf-8-sig") as file:

        file.write("[Script Info]\n")
        file.write("Title: Digital Orbit News\n")
        file.write("ScriptType: v4.00+\n")
        file.write("PlayResX: 1080\n")
        file.write("PlayResY: 1920\n")
        file.write("WrapStyle: 2\n")
        file.write("ScaledBorderAndShadow: yes\n\n")

        file.write("[V4+ Styles]\n")

        file.write(
            "Format: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, "
            "Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        )

        file.write(
            "Style: News,DejaVu Sans,42,"
            "&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            "-1,0,0,0,100,100,0,0,1,3,1,5,80,80,0,1\n\n"
        )

        file.write("[Events]\n")

        file.write(
            "Format: Layer, Start, End, Style, Name, MarginL, "
            "MarginR, MarginV, Effect, Text\n"
        )

        for i, chunk in enumerate(chunks):

            start = duration * i / len(chunks)
            end = duration * (i + 1) / len(chunks)

            first = " ".join(chunk[:2])
            second = " ".join(chunk[2:])

            caption = first

            if second:
                caption += r"\N" + second

            # Escape ASS special characters.
            caption = (
                caption.replace("\\", r"\\")
                .replace("{", r"\{")
                .replace("}", r"\}")
            )

            caption = r"{\an5\pos(540,1080)}" + caption

            file.write(
                f"Dialogue: 0,"
                f"{ass_timestamp(start)},"
                f"{ass_timestamp(end)},"
                f"News,,0,0,0,,{caption}\n"
            )

    print("Subtitles created:", path)
    return True


# =========================================================
# FFMPEG HELPERS
# =========================================================

def run_command(command):

    return subprocess.run(
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


# =========================================================
# CREATE VIDEO
# =========================================================

def make_video(headline, script, index):

    audio_path = VIDEO_DIR / f"voice_{index:02}.mp3"
    ass_path = VIDEO_DIR / f"captions_{index:02}.ass"
    video_path = VIDEO_DIR / f"news_{index:02}.mp4"

    voice = get_voice(index)

    print("Using voice:", voice)

    background_path = create_background(
        headline,
        index
    )

    asyncio.run(
        create_voice(
            script,
            audio_path,
            voice
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
        print("Invalid video duration:", duration)
        return False

    if not create_ass(script, duration, ass_path):
        return False

    ass = filter_path(ass_path)

    video_filter = (
        f"ass=filename='{ass}':"
        "fontsdir='/usr/share/fonts/truetype/dejavu'"
    )

    try:

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

    except subprocess.CalledProcessError as error:

        print("FFmpeg error:")

        if error.stderr:
            print(error.stderr[-3000:])

        raise

    if (
        not video_path.exists()
        or video_path.stat().st_size < 100000
    ):
        raise RuntimeError("Video validation failed.")

    print("VIDEO CREATED:", video_path)

    return True


# =========================================================
# MAIN PROGRAM
# =========================================================

def main():

    print("===================================")
    print("DIGITAL ORBIT USA NEWS")
    print("Batch:", BATCH_NUMBER)
    print("ET release:", ET_PUBLISH_TIME)
    print("Voice offset:", VOICE_OFFSET)
    print("===================================")

    keys = get_api_keys()

    for command in (
        ["ffmpeg", "-version"],
        ["ffprobe", "-version"]
    ):
        if subprocess.run(
            command,
            capture_output=True
        ).returncode != 0:
            raise RuntimeError(command[0] + " is not installed.")

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

        if video_count >= MAX_STORIES:
            break

        if attempted >= MAX_ATTEMPTS:
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

        if not headline or not link or link in seen:
            continue

        seen.add(link)
        attempted += 1

        if len(summary) < 40:
            print("Story skipped: insufficient summary:", headline)
            continue

        prompt = f"""
Write an original 30–60 second English news script
for a general United States audience.

Target length: 90 to 120 words.

Use only the supplied headline and RSS summary.
Do not invent facts, quotes, dates, statistics or sources.
Do not claim independent verification.
Use clear, natural American English.
Make the opening engaging but factual.
End with a short neutral closing sentence.

If information is insufficient, reply exactly:
SKIP_STORY

HEADLINE:
{headline}

RSS SUMMARY:
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

        # The next file number is based on successfully created videos.
        next_index = video_count + 1

        try:

            print("Creating video:", next_index)

            success = make_video(
                headline,
                script,
                next_index
            )

            if success:

                video_count += 1

                scripts.append(
                    f"HEADLINE: {headline}\n"
                    f"SOURCE: {link}\n"
                    f"VOICE: {get_voice(next_index)}\n\n"
                    f"{script}"
                )

                save_scripts()

        except Exception as error:

            print(
                "VIDEO FAILED:",
                headline,
                str(error)[:500]
            )

    save_scripts()

    print("===================================")
    print("BATCH FINISHED")
    print("Batch number:", BATCH_NUMBER)
    print("Videos created:", video_count)
    print("Scripts saved:", len(scripts))
    print("===================================")

    if video_count == 0:
        raise RuntimeError(
            "No videos were created. Check Gemini, RSS and FFmpeg logs."
        )


if __name__ == "__main__":
    main()
