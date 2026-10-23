
import os
import re
import asyncio
import subprocess
import textwrap
from pathlib import Path

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
WIDTH, HEIGHT = 1080, 1920


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
    last_error = None

    for index, key in enumerate(keys, start=1):
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )
            script = (response.text or "").strip()
            if script:
                print(f"Script generated using configured key slot {index}.")
                return script
            print(f"Key slot {index} returned an empty response.")

        except Exception as exc:
            error = str(exc)
            last_error = error
            lower = error.lower()

            quota_error = any(term in lower for term in [
                "429",
                "resource_exhausted",
                "quota exceeded",
                "rate limit",
            ])
            temporary_error = any(term in lower for term in [
                "503",
                "unavailable",
                "server disconnected",
                "deadline exceeded",
            ])

            if quota_error or temporary_error:
                print(
                    f"Key slot {index} unavailable "
                    f"(quota or temporary service issue); trying next key."
                )
                continue

            print(
                f"Key slot {index} failed with a non-retryable error: "
                f"{error[:300]}"
            )
            # Invalid keys or configuration errors are unlikely to improve
            # by trying another key, so stop this story.
            break

    print("All usable API key attempts failed:", (last_error or "")[:300])
    return ""


def font(size, bold=False):
    path = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def create_background(headline, path):
    image = Image.new("RGB", (WIDTH, HEIGHT), (10, 22, 52))
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 110, WIDTH, 132), fill=(235, 45, 55))
    draw.text((65, 180), "DIGITAL ORBIT", font=font(48, True),
              fill="white")
    draw.text((65, 280), "USA NEWS", font=font(40, True),
              fill=(255, 190, 60))

    y = 430
    for line in textwrap.wrap(headline, width=25)[:9]:
        draw.text((65, y), line, font=font(55, True), fill="white")
        y += 82

    draw.rectangle((65, 1430, WIDTH - 65, 1436), fill=(235, 45, 55))
    draw.text((65, 1490), "NEWS UPDATE", font=font(42, True),
              fill=(255, 190, 60))
    draw.text((65, 1570), "Digital Orbit | News Briefing",
              font=font(30), fill=(225, 230, 245))

    image.save(path)


async def create_voice(script, path):
    speech = edge_tts.Communicate(
        script,
        VOICE,
        rate="-5%",
    )
    await speech.save(str(path))


def timestamp(seconds):
    millis = max(0, int(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
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
            file.write(f"{timestamp(start)} --> {timestamp(end)}\n")
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


def make_video(headline, script, index):
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    image_path = VIDEO_DIR / f"background_{index:02}.png"
    audio_path = VIDEO_DIR / f"voice_{index:02}.mp3"
    srt_path = VIDEO_DIR / f"captions_{index:02}.srt"
    video_path = VIDEO_DIR / f"news_{index:02}.mp4"

    create_background(headline, image_path)
    asyncio.run(create_voice(script, audio_path))

    result = subprocess.run(
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

    duration = float(result.stdout.strip())
    if duration < 20 or duration > 65:
        print("Video skipped: voice duration is outside 20–65 seconds.")
        return False

    if not create_srt(script, duration, srt_path):
        return False

    subtitle_filter = (
        f"subtitles={srt_path.as_posix()}:"
        "force_style='FontName=DejaVu Sans,"
        "FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,"
        "Outline=2,Shadow=1,Alignment=2,"
        "MarginV=230,MarginL=65,MarginR=65'"
    )

    run_command([
        "ffmpeg", "-y",
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
    keys = get_api_keys()

    if subprocess.run(
        ["ffmpeg", "-version"], capture_output=True
    ).returncode != 0:
        raise RuntimeError("FFmpeg is not installed.")

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

        headline = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        summary = (entry.get("summary") or "").strip()

        if not headline or not link or link in seen:
            continue
        seen.add(link)

        prompt = f"""
Write an original 30–60 second English news script
for a general US audience, aiming for 90–130 words.

Use only the supplied headline and summary. Do not invent
facts, quotes, dates, or claim independent verification.
If there is not enough information, reply exactly SKIP_STORY.

HEADLINE: {headline}
RSS SUMMARY: {summary}
SOURCE URL: {link}
"""

        script = generate_script(keys, prompt)

        if (
            not script
            or "SKIP_STORY" in script
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
            if make_video(headline, script, len(scripts)):
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
