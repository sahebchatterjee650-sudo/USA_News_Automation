
import os
import re
import asyncio
import subprocess
import textwrap
from pathlib import Path
from datetime import datetime

import feedparser
import edge_tts
from PIL import Image, ImageDraw, ImageFont
from google import genai

RSS_URL = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"
OUTPUT_FILE = "scripts.txt"
VIDEO_DIR = Path("output_videos")

MODEL = "gemini-3.8-flash"
VOICE = "en-US-AndrewNeural"
MAX_STORIES = 3

WIDTH = 1080
HEIGHT = 1920


def get_font(size, bold=False):
    if bold:
        path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    else:
        path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def create_background(headline, image_path):
    image = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = image.load()

    # Simple dark blue gradient background.
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        color = (
            int(8 + 8 * ratio),
            int(20 + 10 * ratio),
            int(48 + 30 * ratio),
        )
        for x in range(WIDTH):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 120, WIDTH, 145), fill=(245, 55, 55))

    draw.text(
        (70, 190),
        "DIGITAL ORBIT",
        font=get_font(48, True),
        fill=(255, 255, 255),
    )

    draw.text(
        (70, 300),
        "USA NEWS",
        font=get_font(38, True),
        fill=(255, 190, 60),
    )

    # Wrap the headline to fit the vertical canvas.
    lines = textwrap.wrap(headline, width=25)
    y = 500

    for line in lines[:8]:
        draw.text(
            (70, y),
            line,
            font=get_font(57, True),
            fill=(255, 255, 255),
        )
        y += 82

    draw.line(
        (70, 1450, WIDTH - 70, 1450),
        fill=(245, 55, 55),
        width=5,
    )

    draw.text(
        (70, 1500),
        "NEWS UPDATE",
        font=get_font(42, True),
        fill=(255, 190, 60),
    )

    draw.text(
        (70, 1580),
        "Follow Digital Orbit for more updates",
        font=get_font(28),
        fill=(220, 225, 240),
    )

    image.save(image_path, quality=95)


def create_srt(script, duration, srt_path):
    # Captions are divided into short readable chunks.
    words = re.findall(r"\S+", script)
    if not words:
        return False

    chunk_size = 6
    chunks = [
        words[i:i + chunk_size]
        for i in range(0, len(words), chunk_size)
    ]

    def timestamp(seconds):
        milliseconds = int(seconds * 1000)
        hours = milliseconds // 3600000
        minutes = (milliseconds % 3600000) // 60000
        seconds_part = (milliseconds % 60000) // 1000
        millis = milliseconds % 1000

        return (
            f"{hours:02}:{minutes:02}:"
            f"{seconds_part:02},{millis:03}"
        )

    with open(srt_path, "w", encoding="utf-8") as file:
        for index, chunk in enumerate(chunks):
            start = duration * index / len(chunks)
            end = duration * (index + 1) / len(chunks)

            file.write(f"{index + 1}\n")
            file.write(
                f"{timestamp(start)} --> {timestamp(end)}\n"
            )
            file.write(" ".join(chunk) + "\n\n")

    return True


def run_command(command):
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=180,
    )


async def create_voice(script, audio_path):
    communicate = edge_tts.Communicate(
        script,
        VOICE,
        rate="-5%",
    )
    await communicate.save(str(audio_path))


def make_video(headline, script, index):
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    prefix = VIDEO_DIR / f"news_{index:02}"
    image_path = VIDEO_DIR / f"background_{index:02}.png"
    audio_path = VIDEO_DIR / f"voice_{index:02}.mp3"
    srt_path = VIDEO_DIR / f"captions_{index:02}.srt"
    video_path = VIDEO_DIR / f"news_{index:02}.mp4"

    print("Creating voice:", headline)
    asyncio.run(create_voice(script, audio_path))

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    duration = float(result.stdout.strip())

    if duration < 20 or duration > 65:
        print("Skipping video: voice duration outside target range.")
        return False

    create_background(headline, image_path)

    if not create_srt(script, duration, srt_path):
        return False

    subtitle_filter = (
        f"subtitles={srt_path.as_posix()}:"
        "force_style='FontName=DejaVu Sans,"
        "FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,"
        "Outline=2,Shadow=1,Alignment=2,"
        "MarginV=230,MarginL=70,MarginR=70'"
    )

    print("Rendering vertical video:", video_path)

    run_command([
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-framerate", "25",
        "-i", str(image_path),
        "-i", str(audio_path),
        "-vf", subtitle_filter,
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


def main():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY secret is missing.")

    if subprocess.run(
        ["ffmpeg", "-version"],
        capture_output=True,
    ).returncode != 0:
        raise RuntimeError("FFmpeg is not installed.")

    client = genai.Client(api_key=api_key)

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        raise RuntimeError("No RSS news received.")

    scripts = []
    seen = set()
    video_count = 0

    for entry in feed.entries:
        if len(scripts) >= MAX_STORIES:
            break

        headline = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        summary = entry.get("summary", "").strip()

        if not headline or not link or link in seen:
            continue

        seen.add(link)

        prompt = f"""
You write concise, factual US English news scripts.

Use only the information supplied below.
Do not invent details, quotes, dates, or facts.
Do not claim that information has been independently verified.
If the supplied information is insufficient, respond exactly:
SKIP_STORY

Write an original spoken news script of 30 to 60 seconds.
Aim for approximately 90 to 130 words.
Use clear language suitable for a general US audience.
Do not include production notes or unsupported conclusions.

HEADLINE: {headline}
RSS SUMMARY: {summary}
SOURCE URL: {link}
"""

        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )
            script = (response.text or "").strip()

            if (
                not script
                or "SKIP_STORY" in script
                or len(script.split()) < 65
            ):
                print("Story skipped:", headline)
                continue

            index = len(scripts) + 1

            scripts.append(
                f"HEADLINE: {headline}\n"
                f"SOURCE: {link}\n\n"
                f"{script}\n"
            )

            print("SCRIPT CREATED:", headline)

            try:
                if make_video(headline, script, index):
                    video_count += 1
            except Exception as exc:
                print("VIDEO FAILED:", headline, str(exc))

        except Exception as exc:
            print("AI REQUEST FAILED:", headline, str(exc))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        file.write("\n---\n\n".join(scripts))

    print(f"Saved {len(scripts)} scripts to {OUTPUT_FILE}")
    print(f"Created {video_count} videos.")

    if not scripts:
        print("No scripts were created.")


if __name__ == "__main__":
    main()
