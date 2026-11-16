
import os
import re
import html
import time
import asyncio
import subprocess
from pathlib import Path
from io import BytesIO
from urllib.parse import quote

import requests

import feedparser
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
from google import genai
from google.genai import types


# =========================================================
# DIGITAL ORBIT - USA NEWS AUTOMATION
# =========================================================

RSS_URL = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"

# Corrected Gemini model
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

VIDEO_DIR = Path("output_videos")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = Path("scripts.txt")

BANNER_FILE = Path("digital_orbit_banner.png")
LOGO_FILE = Path("digital_orbit_logo.png")

MAX_STORIES = int(os.getenv("MAX_STORIES", "6"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "20"))

WIDTH = 1080
HEIGHT = 1920

VOICES = [
    "en-US-AndrewNeural",
    "en-US-JennyNeural"
]

BATCH_NUMBER = os.getenv("BATCH_NUMBER", "1")
ET_PUBLISH_TIME = os.getenv("ET_PUBLISH_TIME", "09:00")
VOICE_OFFSET = int(os.getenv("VOICE_OFFSET", "0"))

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

    if "504" in message or "DEADLINE_EXCEEDED" in message:
        return 504

    if "408" in message or "TIMEOUT" in message or "TIMED OUT" in message:
        return 408

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

        # Hard network timeout: a stuck Gemini request must not consume
        # the entire 60-minute GitHub Actions job.
        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=60000)
        )

        for attempt in range(2):

            try:

                response = client.models.generate_content(
                    model=MODEL,
                    contents=prompt
                )

                script = (response.text or "").strip()

                if script:

                    print(
                        "Script generated using API key:",
                        slot
                    )

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

                    print(
                        "Quota exhausted. Trying next API key."
                    )

                    break

                if code in (408, 500, 503, 504):

                    # Short bounded backoff. Each HTTP request itself has
                    # a 30-second timeout configured above.
                    wait_time = 8 * (attempt + 1)

                    print(
                        "Temporary server error. Waiting:",
                        wait_time,
                        "seconds; retry",
                        attempt + 1,
                        "of 2."
                    )

                    time.sleep(wait_time)
                    continue

                # Permanent errors such as 404 should not
                # be retried repeatedly.
                break

    print("All available API keys failed.")

    return ""


def make_rss_only_script(headline, summary):
    """
    No-cost emergency fallback when Gemini quota/service is unavailable.
    Uses only the supplied RSS headline and summary; it does not add facts.
    This is a templated narration, not an AI-generated or independently
    verified news script.
    """
    clean = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", summary))).strip()
    clean = clean.strip(" .")
    if len(clean.split()) < 25:
        print("RSS fallback skipped: summary is too short for a responsible narration.")
        return ""

    # Keep the source's wording and facts; trim at a sentence boundary.
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    selected = []
    count = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        words = sentence.split()
        if count + len(words) > 105:
            break
        selected.append(sentence)
        count += len(words)
        if count >= 45:
            break

    body = " ".join(selected).strip()
    if len(body.split()) < 25:
        body = " ".join(clean.split()[:85]).strip()

    script = (
        f"Here is the latest update. {headline.strip().rstrip('.')}."
        f" {body} "
        "This report is based on the information currently available in the source summary."
    )
    if len(script.split()) < 45:
        print("RSS fallback skipped: not enough source detail.")
        return ""

    print("Using RSS-only narration fallback because Gemini was unavailable.")
    return script


# =========================================================
# FONT MANAGEMENT
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

            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


# =========================================================
# LOAD LOCAL IMAGES
# =========================================================

def load_local_image(path):

    try:

        if path.exists():

            return Image.open(path).convert("RGB")

    except Exception as error:

        print(
            "Image warning:",
            str(error)[:150]
        )

    return None




# =========================================================
# NEWS IMAGE: LICENSED COMMONS IMAGE, THEN AI FALLBACK
# =========================================================

IMAGE_ATTRIBUTIONS = []

def clean_image_query(headline, summary=""):
    """Build short, topical search terms from both the headline and RSS summary."""
    text = f"{headline} {summary}"
    words = re.findall(r"[A-Za-z0-9]{3,}", text)
    stop = {
        "with", "from", "after", "over", "into", "says", "said", "will",
        "what", "when", "where", "could", "would", "should", "their",
        "about", "this", "that", "have", "been", "were", "they", "them",
        "than", "more", "news", "latest", "update"
    }
    result = []
    for word in words:
        if word.lower() not in stop and word.lower() not in {x.lower() for x in result}:
            result.append(word)
        if len(result) >= 10:
            break
    return " ".join(result) or headline[:100]


def image_search_queries(headline, summary):
    """Use several query variants so one poor headline search does not end the search."""
    primary = clean_image_query(headline, summary)
    headline_only = clean_image_query(headline)
    summary_only = clean_image_query(summary) if summary else ""
    queries = [primary, headline_only, summary_only]
    unique = []
    for query in queries:
        query = query.strip()
        if query and query.lower() not in [q.lower() for q in unique]:
            unique.append(query)
    return unique[:3]


def download_openverse_image(headline, summary, index):
    """Find openly licensed editorial images. Only permissive reuse licenses are accepted."""
    api = "https://api.openverse.org/v1/images/"
    headers = {"User-Agent": "DigitalOrbitNewsBot/1.1 (news video project)"}

    for query in image_search_queries(headline, summary):
        params = {
            "q": query,
            "license": "cc0,pdm,by,by-sa",
            "page_size": 30,
            "mature": "false",
            "filter_dead": "true",
        }
        try:
            response = requests.get(api, params=params, headers=headers, timeout=25)
            response.raise_for_status()
            results = response.json().get("results", [])

            for item in results:
                license_code = (item.get("license") or "").lower()
                if license_code not in {"cc0", "pdm", "by", "by-sa"}:
                    continue

                image_url = item.get("url") or item.get("thumbnail")
                if not image_url:
                    continue

                try:
                    image_response = requests.get(
                        image_url, headers=headers, timeout=25
                    )
                    image_response.raise_for_status()
                    image = Image.open(BytesIO(image_response.content)).convert("RGB")
                    if image.width < 500 or image.height < 300:
                        continue

                    path = VIDEO_DIR / f"news_image_{index:02}.jpg"
                    image.save(path, "JPEG", quality=91)

                    title = html.unescape(item.get("title") or "Untitled image")
                    creator = html.unescape(item.get("creator") or "Creator not listed")
                    license_url = item.get("license_url") or ""
                    landing = item.get("foreign_landing_url") or ""
                    IMAGE_ATTRIBUTIONS.append(
                        f"{title} | {creator} | CC {license_code.upper()} "
                        f"{item.get('license_version') or ''} | {license_url} | {landing}"
                    )
                    print("Openverse licensed image selected:", title, "license:", license_code)
                    return path
                except Exception as image_error:
                    print("Openverse candidate rejected:", str(image_error)[:160])

        except Exception as error:
            print("Openverse search failed for query:", query, str(error)[:180])

    return None


def download_commons_image(headline, summary, index):
    """Fallback search on Wikimedia Commons; keep license and creator attribution."""
    api = "https://commons.wikimedia.org/w/api.php"
    headers = {"User-Agent": "DigitalOrbitNewsBot/1.1 (news video project)"}
    allowed = ("cc0", "public domain", "cc by", "cc-by", "cc by-sa", "cc-by-sa")
    blocked = ("noncommercial", "nc-", "nd-", "no derivatives")

    for query in image_search_queries(headline, summary):
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,
            "gsrlimit": 15,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1400,
        }
        try:
            response = requests.get(api, params=params, headers=headers, timeout=25)
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", {})

            for page in pages.values():
                info = (page.get("imageinfo") or [{}])[0]
                meta = info.get("extmetadata", {})
                license_name = html.unescape(
                    meta.get("LicenseShortName", {}).get("value", "")
                )
                normalized = license_name.lower()
                if not any(term in normalized for term in allowed):
                    continue
                if any(term in normalized for term in blocked):
                    continue

                image_url = info.get("thumburl") or info.get("url")
                if not image_url:
                    continue

                try:
                    img_response = requests.get(image_url, headers=headers, timeout=25)
                    img_response.raise_for_status()
                    image = Image.open(BytesIO(img_response.content)).convert("RGB")
                    if image.width < 500 or image.height < 300:
                        continue

                    path = VIDEO_DIR / f"news_image_{index:02}.jpg"
                    image.save(path, "JPEG", quality=91)
                    author = html.unescape(
                        meta.get("Artist", {}).get("value", "Author not listed")
                    )
                    title = html.unescape(page.get("title", "Wikimedia Commons image"))
                    license_url = meta.get("LicenseUrl", {}).get("value", "")
                    IMAGE_ATTRIBUTIONS.append(
                        f"{title} | {author} | {license_name} | {license_url}"
                    )
                    print("Wikimedia Commons image selected:", title, license_name)
                    return path
                except Exception as image_error:
                    print("Commons candidate rejected:", str(image_error)[:160])

        except Exception as error:
            print("Commons search failed for query:", query, str(error)[:180])

    return None


def create_story_illustration(headline, summary, index):
    """
    Zero-cost local fallback illustration. This is an original designed graphic,
    not a documentary photograph or an AI-generated photo.
    """
    text = f"{headline} {summary}".lower()
    img = Image.new("RGB", (1200, 800), (7, 19, 43))
    draw = ImageDraw.Draw(img)

    # Atmospheric background and layered horizon.
    for y in range(800):
        blend = y / 800
        color = (
            int(8 + 12 * blend),
            int(23 + 17 * blend),
            int(48 + 25 * blend)
        )
        draw.line((0, y, 1200, y), fill=color)

    draw.ellipse((850, 55, 1110, 315), fill=(24, 75, 115))
    draw.ellipse((900, 95, 1060, 255), fill=(35, 110, 150))
    draw.rounded_rectangle((55, 55, 1145, 745), radius=34,
                           outline=(50, 190, 230), width=5)

    # Identify the broad subject and draw a clearly illustrative visual.
    if any(k in text for k in ("storm", "hurricane", "weather", "snow", "winter", "el niño", "flood")):
        label = "WEATHER & CLIMATE"
        # clouds, rain and wind
        for x, y, w in [(250, 290, 210), (520, 245, 250), (790, 315, 190)]:
            draw.ellipse((x, y, x+w, y+115), fill=(135, 165, 190))
            draw.ellipse((x+45, y-55, x+155, y+90), fill=(155, 185, 205))
        for x in range(300, 900, 90):
            draw.line((x, 440, x-45, 545), fill=(65, 190, 245), width=9)
        draw.polygon([(0, 650), (280, 535), (520, 650), (790, 530),
                      (1200, 660), (1200, 800), (0, 800)], fill=(20, 65, 90))
    elif any(k in text for k in ("space", "spacex", "rocket", "nasa", "satellite", "starship")):
        label = "SPACE & TECHNOLOGY"
        draw.ellipse((760, 190, 920, 350), outline=(110, 210, 255), width=5)
        draw.ellipse((800, 230, 880, 310), fill=(45, 120, 165))
        draw.polygon([(535, 180), (635, 420), (600, 570), (565, 420)],
                     fill=(220, 230, 240))
        draw.polygon([(535, 180), (500, 430), (565, 420)], fill=(235, 75, 70))
        draw.polygon([(565, 420), (600, 570), (635, 420)], fill=(255, 170, 45))
        draw.ellipse((570, 350, 600, 410), fill=(60, 170, 235))
        for x, y in [(220,180),(950,420),(300,500),(1030,170)]:
            draw.ellipse((x,y,x+10,y+10), fill="white")
    elif any(k in text for k in ("court", "judge", "lawsuit", "legal", "supreme", "trial", "congress", "senate", "election", "president", "trump", "politic")):
        label = "POLITICS & PUBLIC AFFAIRS"
        draw.rectangle((250, 360, 950, 625), fill=(25, 55, 85))
        draw.polygon([(220, 360), (600, 205), (980, 360)], fill=(210, 220, 230))
        for x in (330, 475, 620, 765):
            draw.rectangle((x, 365, x+65, 625), fill=(185, 205, 220))
        draw.rectangle((220, 625, 980, 665), fill=(220, 225, 230))
        draw.rectangle((180, 665, 1020, 700), fill=(120, 155, 180))
    elif any(k in text for k in ("crash", "collision", "flight", "airport", "airline", "ship", "train", "transport", "road")):
        label = "TRANSPORT & INFRASTRUCTURE"
        draw.rectangle((0, 575, 1200, 800), fill=(24, 42, 63))
        draw.polygon([(250, 470), (850, 470), (1030, 590), (80, 590)], fill=(180, 195, 205))
        draw.rectangle((500, 360, 720, 470), fill=(225, 235, 240))
        draw.polygon([(500, 390), (360, 435), (500, 435)], fill=(90, 170, 210))
        draw.polygon([(720, 390), (865, 435), (720, 435)], fill=(90, 170, 210))
        draw.rectangle((590, 330, 630, 365), fill=(220, 225, 230))
    elif any(k in text for k in ("fire", "explosion", "disaster", "emergency", "police", "shooting", "attack")):
        label = "PUBLIC SAFETY"
        draw.polygon([(0, 650), (180, 430), (330, 650), (520, 390),
                      (740, 650), (930, 440), (1200, 670), (1200, 800), (0,800)],
                     fill=(75, 55, 62))
        for x, y in [(300, 460), (600, 500), (850, 455)]:
            draw.polygon([(x, y), (x-65, y+140), (x, y+105), (x+60, y+155)],
                         fill=(245, 115, 35))
            draw.polygon([(x, y+35), (x-30, y+125), (x+5, y+105), (x+35, y+145)],
                         fill=(255, 205, 65))
    else:
        label = "USA NEWS | EXPLAINER"
        # Abstract newsroom / data visualization for other topics.
        draw.rounded_rectangle((220, 300, 980, 630), radius=28, fill=(16, 48, 78),
                               outline=(60, 155, 205), width=4)
        draw.line((300, 550, 420, 465, 535, 500, 690, 370, 850, 420),
                  fill=(65, 215, 240), width=12)
        for x, y in [(300,550),(420,465),(535,500),(690,370),(850,420)]:
            draw.ellipse((x-13,y-13,x+13,y+13), fill=(255,205,75))

    draw.rounded_rectangle((85, 90, 620, 160), radius=16, fill=(185, 25, 43))
    draw.text((115, 105), label, font=get_font(32, True), fill="white")
    draw.text((90, 690), "EDITORIAL ILLUSTRATION • DIGITAL ORBIT",
              font=get_font(24, True), fill=(185, 225, 240))

    path = VIDEO_DIR / f"news_image_{index:02}.jpg"
    img.save(path, "JPEG", quality=90)
    print("Created zero-cost topic illustration (not a real photo):", label)
    return path


def get_news_image(headline, summary, index):
    image_path = download_openverse_image(headline, summary, index)
    if image_path:
        return image_path

    print("No suitable Openverse image. Trying Wikimedia Commons.")
    image_path = download_commons_image(headline, summary, index)
    if image_path:
        return image_path

    print("No openly licensed real image found. Creating a topic-specific editorial illustration.")
    return create_story_illustration(headline, summary, index)


def save_image_attributions():
    if IMAGE_ATTRIBUTIONS:
        (VIDEO_DIR / "image_attributions.txt").write_text(
            "\n".join(IMAGE_ATTRIBUTIONS),
            encoding="utf-8"
        )



# =========================================================
# DIGITAL ORBIT VIDEO BACKGROUND
# =========================================================

def create_background(headline, index, news_image=None):

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

        background = Image.blend(
            background,
            dark,
            0.62
        )

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

    # Header
    od.rectangle(
        (0, 0, WIDTH, 275),
        fill=(2, 10, 28, 220)
    )

    # Subtitle panel
    od.rounded_rectangle(
        (38, 390, WIDTH - 38, 1500),
        radius=38,
        fill=(3, 16, 39, 225),
        outline=(35, 150, 225, 255),
        width=4
    )

    # Footer
    od.rectangle(
        (0, 1620, WIDTH, HEIGHT),
        fill=(2, 8, 22, 225)
    )

    background = Image.alpha_composite(
        background.convert("RGBA"),
        overlay
    ).convert("RGB")

    draw = ImageDraw.Draw(background)

    # Logo
    logo = load_local_image(LOGO_FILE)

    if logo is not None:

        logo.thumbnail(
            (145, 145),
            Image.Resampling.LANCZOS
        )

        background.paste(
            logo,
            (45, 45)
        )

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

    # Headline wrapping
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

    # Story-specific editorial image panel
    if news_image is not None and Path(news_image).exists():
        try:
            photo = Image.open(news_image).convert("RGB")
            photo = ImageOps.fit(
                photo, (920, 600),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.45)
            )
            background.paste(photo, (80, 820))
            draw = ImageDraw.Draw(background)
            draw.rounded_rectangle(
                (80, 820, 1000, 1420),
                radius=18, outline=(45, 210, 255), width=5
            )
        except Exception as error:
            print("Could not place news image:", str(error)[:200])

    draw.text(
        (WIDTH // 2, 1445),
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


# =========================================================
# VOICE GENERATION
# =========================================================

async def create_voice(script, path, voice):

    # Edge TTS occasionally returns an empty response or a transient
    # network error. Retry a few times before marking this story failed.
    last_error = None

    for attempt in range(3):

        try:
            if path.exists():
                path.unlink()

            communicator = edge_tts.Communicate(
                script,
                voice,
                rate="-5%"
            )

            await asyncio.wait_for(
                communicator.save(str(path)),
                timeout=150
            )

            if path.exists() and path.stat().st_size > 5000:
                return

            raise RuntimeError("Edge TTS returned no audio or an empty audio file.")

        except Exception as error:
            last_error = error
            print(
                f"Edge TTS attempt {attempt + 1}/3 failed:",
                str(error)[:300]
            )

            if path.exists():
                path.unlink()

            if attempt < 2:
                await asyncio.sleep(4 * (attempt + 1))

    raise RuntimeError(f"Edge TTS failed after 3 attempts: {last_error}")


def get_voice(index):

    global_index = VOICE_OFFSET + index - 1

    return VOICES[global_index % len(VOICES)]


# =========================================================
# SUBTITLE TIMESTAMP
# =========================================================

def ass_timestamp(seconds):

    centiseconds = max(
        0,
        int(seconds * 100)
    )

    hours, centiseconds = divmod(
        centiseconds,
        360000
    )

    minutes, centiseconds = divmod(
        centiseconds,
        6000
    )

    secs, centiseconds = divmod(
        centiseconds,
        100
    )

    return (
        f"{hours}:"
        f"{minutes:02}:"
        f"{secs:02}."
        f"{centiseconds:02}"
    )


# =========================================================
# CREATE ASS SUBTITLES
# =========================================================

def create_ass(script, duration, path):

    words = script.split()

    if not words:
        return False

    chunks = [
        words[i:i + 4]
        for i in range(0, len(words), 4)
    ]

    with open(
        path,
        "w",
        encoding="utf-8-sig"
    ) as file:

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

            # Escape literal ASS control characters first.
            first = (
                first.replace("\\", r"\\")
                .replace("{", r"\{")
                .replace("}", r"\}")
            )

            second = (
                second.replace("\\", r"\\")
                .replace("{", r"\{")
                .replace("}", r"\}")
            )

            caption = first

            if second:
                caption += r"\N" + second

            caption = (
                r"{\an5\pos(540,1515)}"
                + caption
            )

            file.write(
                f"Dialogue: 0,"
                f"{ass_timestamp(start)},"
                f"{ass_timestamp(end)},"
                f"News,,0,0,0,,{caption}\n"
            )

    print("ASS subtitles created:", path)

    return True


# =========================================================
# FFMPEG HELPERS
# =========================================================

def run_command(command):

    result = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300
    )

    return result


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
# MAKE VIDEO
# =========================================================

def make_video(headline, script, index, news_image=None):

    audio_path = VIDEO_DIR / f"voice_{index:02}.mp3"

    ass_path = VIDEO_DIR / f"captions_{index:02}.ass"

    video_path = VIDEO_DIR / f"news_{index:02}.mp4"

    voice = get_voice(index)

    print("Selected voice:", voice)

    background_path = create_background(
        headline,
        index,
        news_image
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

        print(
            "Video skipped: invalid duration:",
            duration
        )

        return False

    if not create_ass(
        script,
        duration,
        ass_path
    ):
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

        print("FFmpeg video error:")

        if error.stderr:
            print(error.stderr[-3000:])

        raise

    if (
        not video_path.exists()
        or video_path.stat().st_size < 100000
    ):

        raise RuntimeError(
            "Video validation failed."
        )

    print("VIDEO CREATED:", video_path)

    return True


# =========================================================
# MAIN PROGRAM
# =========================================================

def main():

    print("===================================")
    print("DIGITAL ORBIT USA NEWS")
    print("Gemini model:", MODEL)
    print("Batch:", BATCH_NUMBER)
    print("ET release:", ET_PUBLISH_TIME)
    print("Voice offset:", VOICE_OFFSET)
    print("===================================")

    keys = get_api_keys()

    # Check FFmpeg tools
    for command in (
        ["ffmpeg", "-version"],
        ["ffprobe", "-version"]
    ):

        result = subprocess.run(
            command,
            capture_output=True
        )

        if result.returncode != 0:

            raise RuntimeError(
                command[0] + " is not installed."
            )

    feed = feedparser.parse(RSS_URL)

    if not feed.entries:

        raise RuntimeError(
            "No RSS news received."
        )

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

        (
            VIDEO_DIR / "scripts.txt"
        ).write_text(
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
Write an original 30-60 second English news script
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

        script = generate_script(
            keys,
            prompt
        )

        used_rss_fallback = False
        if not script:
            script = make_rss_only_script(headline, summary)
            used_rss_fallback = bool(script)

        minimum_words = 45 if used_rss_fallback else 65

        if (
            not script
            or "SKIP_STORY" in script.upper()
            or len(script.split()) < minimum_words
        ):

            print("Story skipped: insufficient safe script content:", headline)

            continue

        next_index = video_count + 1

        try:

            print(
                "Creating video:",
                next_index
            )

            news_image = get_news_image(
                headline,
                summary,
                next_index
            )

            if news_image is None:
                print("No free editorial image found; using branded headline graphic for:", headline)

            success = make_video(
                headline,
                script,
                next_index,
                news_image
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
    save_image_attributions()

    print("===================================")
    print("BATCH FINISHED")
    print("Gemini model:", MODEL)
    print("Batch number:", BATCH_NUMBER)
    print("Videos created:", video_count)
    print("Scripts saved:", len(scripts))
    print("===================================")

    if video_count == 0:

        raise RuntimeError(
            "No videos were created. "
            "Check Gemini model, API access, RSS and FFmpeg logs."
        )


if __name__ == "__main__":
    main()
