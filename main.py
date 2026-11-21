
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

# Gemini is optional. The RSS-only fallback keeps the pipeline running
# when no API key is configured or Gemini is unavailable.
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


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

    # No key is required: RSS-only narration is the no-Gemini fallback.
    print("API keys loaded:", len(keys), "(Gemini optional)")

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

    if not keys or genai is None or types is None:
        print("Gemini disabled or unavailable; using RSS-only fallback.")
        return ""

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


def fetch_source_article_text(url):
    """Fetch readable publisher paragraphs; never invent or infer missing facts."""
    if not url:
        return ""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DigitalOrbitNewsBot/1.0)"},
            timeout=12
        )
        response.raise_for_status()
        page = response.text
        page = re.sub(r"(?is)<(script|style|nav|footer|header|aside)\b[^>]*>.*?</\1>", " ", page)
        paragraphs = re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", page)
        cleaned = []
        for paragraph in paragraphs:
            value = html.unescape(re.sub(r"<[^>]+>", " ", paragraph))
            value = re.sub(r"\s+", " ", value).strip()
            if len(value.split()) >= 8:
                cleaned.append(value)
        return " ".join(" ".join(cleaned).split()[:220])
    except Exception as error:
        print("Source article fetch unavailable:", str(error)[:180])
        return ""


def make_rss_only_script(headline, summary):
    """
    No-cost fallback using only text supplied by RSS and the linked publisher.
    This is templated narration, not AI-generated or independently verified.
    """
    clean = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", summary))).strip()
    clean = clean.strip(" .")
    if len(clean.split()) < 25:
        print("RSS fallback skipped: summary is too short for a responsible narration.")
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", clean)
    selected = []
    count = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence_words = sentence.split()
        if count + len(sentence_words) > 125:
            break
        selected.append(sentence)
        count += len(sentence_words)
        if count >= 85:
            break

    body = " ".join(selected).strip()
    if len(body.split()) < 45:
        print("RSS fallback skipped: linked article did not provide enough usable detail.")
        return ""

    # Lead with the source's first concrete fact rather than a generic intro.
    script = (
        f"{body} "
        "This report is based on information published by the source."
    )
    if len(script.split()) < 55:
        print("RSS fallback skipped: source text is too short for a responsible short report.")
        return ""

    print("Using source-backed RSS narration fallback because Gemini was unavailable.")
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
    elif any(k in text for k in ("nvidia", "jensen huang", "semiconductor", "microchip", "chipmaker", "artificial intelligence", " ai ", "technology company", "tech company", "tech firm", "software company")):
        label = "TECHNOLOGY & AI"
        # Original chip-and-circuit illustration for technology stories.
        draw.rounded_rectangle((260, 260, 940, 620), radius=42,
                               fill=(15, 55, 92), outline=(60, 205, 245), width=8)
        draw.rounded_rectangle((430, 340, 770, 540), radius=28,
                               fill=(20, 95, 145), outline=(120, 225, 255), width=6)
        draw.text((600, 440), "AI", font=get_font(110, True),
                  fill=(225, 245, 255), anchor="mm")
        for x in range(300, 901, 100):
            draw.line((x, 210, x, 260), fill=(65, 210, 245), width=8)
            draw.line((x, 620, x, 675), fill=(65, 210, 245), width=8)
        for y in (330, 440, 550):
            draw.line((205, y, 260, y), fill=(65, 210, 245), width=8)
            draw.line((940, y, 995, y), fill=(65, 210, 245), width=8)
        for x, y in ((345, 310), (850, 570), (345, 570), (850, 310)):
            draw.ellipse((x-13, y-13, x+13, y+13), fill=(255, 205, 75))
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

    # Broadcast-style vertical layout: masthead, headline, hero image,
    # lower-third, caption-safe area and a separate ticker strip.
    background = Image.new("RGB", (WIDTH, HEIGHT), (4, 13, 31))
    draw = ImageDraw.Draw(background)

    for y in range(HEIGHT):
        blend = y / HEIGHT
        color = (
            int(4 + 5 * blend),
            int(14 + 12 * blend),
            int(34 + 24 * blend)
        )
        draw.line((0, y, WIDTH, y), fill=color)

    # Subtle broadcast grid / light accents.
    draw.rectangle((0, 0, WIDTH, 235), fill=(2, 9, 23))
    draw.rectangle((0, 230, WIDTH, 238), fill=(25, 170, 225))

    logo = load_local_image(LOGO_FILE)
    if logo is not None:
        logo.thumbnail((142, 142), Image.Resampling.LANCZOS)
        background.paste(logo, (42, 38))
        draw = ImageDraw.Draw(background)
        title_x = 215
    else:
        title_x = 55

    draw.text((title_x, 48), "DIGITAL ORBIT",
              font=get_font(58, True), fill=(250, 252, 255))
    draw.text((title_x, 125), "USA NEWS  |  NEWS BRIEFING",
              font=get_font(31, True), fill=(40, 210, 245))

    draw.rounded_rectangle((790, 65, 1035, 130), radius=16,
                           fill=(190, 24, 45))
    draw.text((912, 97), "LIVE UPDATE", font=get_font(27, True),
              fill="white", anchor="mm")

    # Headline panel.
    draw.rounded_rectangle((38, 275, 1042, 565), radius=26,
                           fill=(7, 25, 52), outline=(35, 165, 220), width=4)
    draw.text((75, 305), "TOP STORY", font=get_font(28, True),
              fill=(255, 198, 65))

    headline_font = get_font(43, True)
    words = headline.upper().split()
    lines, current = [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if draw.textbbox((0, 0), trial, font=headline_font)[2] <= 900:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    # Fit long headlines safely into the panel.
    if len(lines) > 4:
        headline_font = get_font(36, True)
        lines, current = [], ""
        for word in words:
            trial = (current + " " + word).strip()
            if draw.textbbox((0, 0), trial, font=headline_font)[2] <= 920:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)

    y = 355 if len(lines) <= 3 else 340
    line_gap = 52 if len(lines) <= 3 else 46
    for line in lines[:4]:
        draw.text((540, y), line, font=headline_font,
                  fill=(248, 250, 255), anchor="mt")
        y += line_gap

    # Main editorial image, using licensed image or clearly labeled illustration.
    draw.rounded_rectangle((38, 600, 1042, 1285), radius=24,
                           fill=(9, 27, 52), outline=(35, 185, 230), width=4)
    if news_image is not None and Path(news_image).exists():
        try:
            photo = Image.open(news_image).convert("RGB")
            photo = ImageOps.fit(
                photo, (960, 635),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.45)
            )
            background.paste(photo, (60, 625))
        except Exception as error:
            print("Could not place news image:", str(error)[:200])
            draw.text((540, 940), "NEWS VISUAL", font=get_font(48, True),
                      fill=(210, 230, 245), anchor="mm")
    else:
        draw.text((540, 940), "NEWS VISUAL", font=get_font(48, True),
                  fill=(210, 230, 245), anchor="mm")

    draw = ImageDraw.Draw(background)
    draw.rounded_rectangle((60, 625, 1020, 1260), radius=18,
                           outline=(55, 195, 235), width=4)

    # Leave the lower-third clear; FFmpeg animates the red breaking-news
    # banner and its title onto the screen during the first 0.6 seconds.
    draw.rectangle((0, 1310, WIDTH, 1420), fill=(7, 19, 40))
    draw.rectangle((0, 1420, WIDTH, 1427), fill=(25, 170, 225))
    draw.text((55, 1450), "DIGITAL ORBIT  •  USA NEWS",
              font=get_font(28, True), fill=(55, 210, 245))
    draw.rounded_rectangle((38, 1495, 1042, 1660), radius=18,
                           fill=(5, 18, 39), outline=(30, 125, 175), width=2)
    draw.text((540, 1685), "STAY INFORMED  |  NEWS BRIEFING",
              font=get_font(25, True), fill=(205, 225, 240), anchor="mm")

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
                rate="-12%"
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
            "Style: News,DejaVu Sans,40,"
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
                r"{\an5\pos(540,1575)}"
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

    if duration < 15 or duration > 60:

        print(
            "Video skipped: narration must be at least 15 seconds and no more than 60 seconds; actual duration:",
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

    # Subtle Ken Burns movement, broadcast-style color finishing,
    # clean fade-in/out, and readable burned-in captions.
    video_filter = (
        "zoompan="
        f"z='1+0.12*on/{max(1, int(duration * 25))}':"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        "d=1:s=1080x1920:fps=25,"
        "eq=contrast=1.045:saturation=1.08:brightness=-0.008,"
        "unsharp=5:5:0.35:3:3:0.15,"
        "vignette=PI/5,"
        f"ass=filename='{ass}':"
        "fontsdir='/usr/share/fonts/truetype/dejavu',"
        "fade=t=in:st=0:d=0.35,"
        f"fade=t=out:st={max(0, duration - 0.45):.3f}:d=0.45"
    )

    # Broadcast motion package: animated lower-third slides in, headline
    # label follows it, and a thin cyan accent sweeps across the top.
    # All elements are generated locally by FFmpeg; no paid API or asset.
    lower_third_filter = (
        "drawbox=x='-w+min(t/0.55\\,1)*w':y=1310:w=1080:h=110:"
        "color=0xBE162D@1:t=fill,"
        "drawtext=text='BREAKING NEWS':"
        "fontfile='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf':"
        "fontsize=43:fontcolor=white:"
        "x='-tw+min(t/0.55\\,1)*135':y=1340,"
        "drawbox=x=0:y=230:w='min(t/0.8\\,1)*1080':h=7:"
        "color=0x19AAE1@1:t=fill"
    )
    video_filter += "," + lower_third_filter

    audio_filter = "highpass=f=75,loudnorm=I=-16:TP=-1.5:LRA=11"

    # Scrolling headline ticker is rendered by FFmpeg from a temporary text file.
    ticker_path = VIDEO_DIR / f"ticker_{index:02}.txt"
    ticker_path.write_text(
        f"BREAKING NEWS  •  {headline.upper()}  •  FOLLOW DIGITAL ORBIT FOR UPDATES",
        encoding="utf-8"
    )
    ticker_file = filter_path(ticker_path)
    ticker_filter = (
        f"drawtext=textfile='{ticker_file}':reload=0:"
        "fontfile='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf':"
        "fontsize=30:fontcolor=white:"
        "box=1:boxcolor=0x07172D@1:boxborderw=18:"
        "x='w-mod(t*170\\,w+tw)':y=1740"
    )
    video_filter += "," + ticker_filter

    try:

        run_command([
            "ffmpeg",
            "-y",
            "-loop", "1",
            "-framerate", "25",
            "-i", str(background_path),
            "-i", str(audio_path),
            "-vf", video_filter,
            "-af", audio_filter,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "21",
            "-pix_fmt", "yuv420p",
            "-r", "25",
            "-c:a", "aac",
            "-b:a", "160k",
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

    if not keys:
        print("No Gemini API key configured. RSS-only mode is active.")

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
    seen_headlines = set()

    # Compare against recent videos already on the YouTube channel, not only
    # this workflow run, so the next scheduled batch cannot repost the same headline.
    from youtube_upload import get_recent_video_titles, normalize_title
    try:
        published_titles = get_recent_video_titles()
    except Exception as duplicate_check_error:
        raise RuntimeError(
            "Could not check recent YouTube uploads. Re-authorize the app with "
            "youtube.readonly and youtube.upload scopes before running automation."
        ) from duplicate_check_error

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

        # RSS descriptions are often brief; use the linked publisher article text too.
        article_text = fetch_source_article_text(link)
        if article_text:
            summary = (summary + " " + article_text).strip()

        normalized_headline = normalize_title(headline)

        if (
            not headline
            or not link
            or link in seen
            or not normalized_headline
            or normalized_headline in seen_headlines
        ):
            continue

        seen.add(link)
        seen_headlines.add(normalized_headline)
        attempted += 1

        if normalized_headline in published_titles:
            print("DUPLICATE SKIPPED: already present on YouTube:", headline)
            continue

        if len(summary) < 40:

            print(
                "Story skipped: insufficient summary:",
                headline
            )

            continue

        prompt = f"""
Write an original, high-retention YouTube Shorts news script
for a general United States audience.

Target length: 85 to 105 spoken words, usually about 30-45 seconds.
The FIRST sentence must be a specific, compelling factual hook tied to this story.
Do not begin with "Here is the latest update", "In today's news", or "You won't believe".
Use this structure:
1. First 1-2 seconds: the most important surprising or useful verified fact.
2. Next: explain what happened in plain American English.
3. Then: give the key context, number, consequence, or next step only if present in the source.
4. End with one concise, natural line that gives viewers a reason to follow future verified updates; do not beg for likes.

Use only the supplied headline and source text.
Do not invent facts, quotes, dates, statistics, motives, or sources.
Do not repeat the headline as the opening.
Avoid filler, generic transitions, and sensational claims.
Keep sentences short and easy to follow when heard once.
Do not add a greeting or channel introduction.
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

        used_rss_fallback = False
        if not script:
            script = make_rss_only_script(headline, summary)
            used_rss_fallback = bool(script)

        minimum_words = 55 if used_rss_fallback else 65

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

                # Upload immediately after each MP4 is rendered.
                # YouTube stores the video and publishes it at its scheduled time.
                try:
                    from youtube_upload import upload_video
                    youtube_result = upload_video(
                        video_path=VIDEO_DIR / f"news_{next_index:02}.mp4",
                        headline=headline,
                        source_url=link,
                        index=video_count
                    )
                    print("YOUTUBE UPLOAD SUCCESS:", youtube_result)
                except Exception as upload_error:
                    print(
                        "YOUTUBE UPLOAD FAILED:",
                        headline,
                        str(upload_error)[:700]
                    )
                    # Do not silently continue after a failed upload.
                    # Stop the workflow so the failure is visible in Actions.
                    raise SystemExit(
                        "YouTube upload failed; stopping this batch."
                    ) from upload_error

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
            "Check source article access, narration length, RSS and FFmpeg logs."
        )


if __name__ == "__main__":
    main()
