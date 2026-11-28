
import os
import re
import html
import hashlib
import difflib
import json
import textwrap
import time
import asyncio
import subprocess
import shutil
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

# Multiple established US news sources plus AP/Reuters discovery feeds.
# Each feed is fetched independently so one provider outage cannot stop the batch.
RSS_FEEDS = [
    ("BBC US & Canada", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"),
    ("NPR", "https://feeds.npr.org/1001/rss.xml"),
    ("ABC News", "https://abcnews.com/abcnews/topstories"),
    ("CBS News", "https://www.cbsnews.com/latest/rss/main"),
    ("NBC News", "https://feeds.nbcnews.com/nbcnews/public/news"),
    ("AP via Google News", "https://news.google.com/rss/search?q=when:24h+site:apnews.com&hl=en-US&gl=US&ceid=US:en"),
    ("Reuters via Google News", "https://news.google.com/rss/search?q=when:24h+site:reuters.com&hl=en-US&gl=US&ceid=US:en"),
]

# Corrected Gemini model
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

VIDEO_DIR = Path("output_videos")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = Path("scripts.txt")

BANNER_FILE = Path("digital_orbit_banner.png")
LOGO_FILE = Path("digital_orbit_logo.png")

MAX_STORIES = int(os.getenv("MAX_STORIES", "2"))
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

                active_model = MODEL
                try:
                    response = client.models.generate_content(
                        model=active_model,
                        contents=prompt
                    )
                except Exception as primary_error:
                    primary_code = error_code(primary_error)
                    fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash")
                    if primary_code in (408, 500, 503, 504) and fallback_model and fallback_model != active_model:
                        print("Primary Gemini model unavailable; trying fallback model:", fallback_model)
                        response = client.models.generate_content(
                            model=fallback_model,
                            contents=prompt
                        )
                        active_model = fallback_model
                    else:
                        raise

                script = (response.text or "").strip()

                if script:

                    print(
                        "Script generated using model and API key:",
                        active_model, slot
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


def fetch_multi_source_news():
    """Collect recent candidate stories from several established US news feeds."""
    entries = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DigitalOrbitNewsBot/1.2)"}
    for source_name, feed_url in RSS_FEEDS:
        try:
            response = requests.get(feed_url, headers=headers, timeout=15)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            source_entries = parsed.entries or []
            print("RSS source:", source_name, "| stories:", len(source_entries))
            for item in source_entries:
                item = dict(item)
                item["_digital_orbit_source"] = source_name
                entries.append(item)
        except Exception as error:
            print("RSS source unavailable:", source_name, str(error)[:180])

    def published_key(item):
        parsed_time = item.get("published_parsed") or item.get("updated_parsed")
        if parsed_time:
            try:
                return time.mktime(parsed_time)
            except Exception:
                pass
        return 0

    entries.sort(key=published_key, reverse=True)
    print("Total RSS candidates collected:", len(entries))
    return entries


def fetch_google_trends_us():
    """Load US trending searches as discovery hints, never as factual sources."""
    url = "https://trends.google.com/trending/rss?geo=US"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DigitalOrbitTrendDiscovery/1.0)"},
            timeout=12
        )
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        topics = []
        for item in parsed.entries[:40]:
            title = re.sub(r"\s+", " ", html.unescape(item.get("title", ""))).strip()
            if title and title.lower() not in {x.lower() for x in topics}:
                topics.append(title)
        print("US Google Trends topics loaded:", len(topics))
        if topics:
            print("Trend discovery signals:", " | ".join(topics[:12]))
        return topics
    except Exception as error:
        print("Google Trends unavailable; continuing with publisher recency:", str(error)[:180])
        return []


def rank_stories_by_trend_interest(entries, trending_topics):
    """Rank by meaningful topic overlap; unrelated trends never receive credit."""
    from news_quality_gate import trend_relevance
    if not trending_topics:
        for item in entries:
            item["_trend_score"] = 0
            item["_trend_match"] = ""
        return entries

    ranked = []
    for original_index, item in enumerate(entries):
        headline = item.get("title", "")
        matches = [(trend_relevance(headline, topic), topic) for topic in trending_topics]
        score, topic = max(matches, default=(0.0, ""))
        item["_trend_score"] = round(score * 6, 2) if score else 0
        item["_trend_match"] = topic if score else ""
        ranked.append((score, original_index, item))

    ranked.sort(key=lambda row: (-row[0], row[1]))
    matched = sum(1 for score, _, _ in ranked if score > 0)
    print("Publisher stories with meaningful US trend matches:", matched)
    for score, _, item in ranked[:8]:
        if score:
            print("TREND PRIORITY:", item.get("title", "")[:120],
                  "| matched trend:", item.get("_trend_match", ""),
                  "| relevance:", round(score, 3))
    return [item for _, _, item in ranked]

def score_viral_opportunity(entry):
    """
    Editorial prioritization score (0-100), NOT a prediction of virality.
    Uses only RSS headline, publisher timestamp and Google Trends overlap.
    """
    headline = (entry.get("title") or "").strip()
    text = headline.lower()
    score = 0
    reasons = []

    # Current US search interest is a discovery signal, never evidence of truth.
    trend_score = min(30, int(entry.get("_trend_score", 0)) * 5)
    if trend_score:
        score += trend_score
        reasons.append("US trend match")

    # Give newer publisher items priority; unknown timestamps receive a neutral score.
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        try:
            age_hours = max(0, (time.time() - time.mktime(published)) / 3600)
            freshness = max(0, 25 - int(age_hours / 2))
            score += freshness
            if freshness >= 18:
                reasons.append("very recent")
            elif freshness:
                reasons.append("recent")
        except Exception:
            score += 10
    else:
        score += 10

    # Practical impact topics often matter to a broad US audience.
    impact_terms = (
        "election", "vote", "president", "congress", "senate", "supreme court",
        "law", "bill", "tax", "inflation", "prices", "gas", "jobs", "layoffs",
        "interest rate", "mortgage", "social security", "medicare", "health",
        "safety", "warning", "storm", "hurricane", "wildfire", "earthquake",
        "school", "student", "airport", "travel", "shutdown", "strike"
    )
    matched_impact = [term for term in impact_terms if term in text]
    if matched_impact:
        score += min(20, 8 + (len(matched_impact) - 1) * 4)
        reasons.append("public impact")

    # Specific names, figures or dates make a headline easier to understand.
    if re.search(r"\d", headline):
        score += 8
        reasons.append("specific detail")
    if 6 <= len(headline.split()) <= 16:
        score += 7
    elif 4 <= len(headline.split()) <= 20:
        score += 4

    score = min(100, score)
    entry["_growth_score"] = score
    entry["_growth_reasons"] = reasons
    return score


def rank_stories_for_growth(entries):
    """Prioritize explainable, timely, relevant stories without hype or paid APIs."""
    ranked = []
    for original_index, entry in enumerate(entries):
        score = score_viral_opportunity(entry)
        ranked.append((score, original_index, entry))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    print("EDITORIAL GROWTH PRIORITY (not a virality guarantee):")
    for score, _, entry in ranked[:10]:
        print("GROWTH SCORE:", score, "|",
              ", ".join(entry.get("_growth_reasons", [])) or "baseline",
              "|", (entry.get("title") or "")[:140])
    return [entry for _, _, entry in ranked]


def save_growth_priority_report(entries):
    """Save an auditable shortlist so editors can compare priorities with outcomes."""
    lines = [
        "DIGITAL ORBIT — EDITORIAL GROWTH PRIORITY",
        "Scores are ranking hints only, not predicted views or a virality guarantee.",
        "Google Trends is a discovery signal, not a factual source.",
        ""
    ]
    for number, entry in enumerate(entries[:15], 1):
        reasons = ", ".join(entry.get("_growth_reasons", [])) or "baseline"
        lines.extend([
            f"{number}. SCORE {entry.get('_growth_score', 0)}/100 — {entry.get('title', '').strip()}",
            f"Reason: {reasons}",
            f"Trend match: {entry.get('_trend_match', '') or 'none'}",
            f"Source: {entry.get('link', '')}",
            ""
        ])
    report = VIDEO_DIR / "growth_priority_report.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("EDITORIAL GROWTH REPORT SAVED:", report)


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
    Create a concise retention-oriented narration from publisher text.
    Accepts feed-style fragments, but never invents supporting facts.
    """
    raw = html.unescape(re.sub(r"<[^>]+>", " ", summary or ""))
    raw = re.sub(r"\s+", " ", raw).strip()
    title = re.sub(r"\s+", " ", html.unescape(headline or "")).strip(" .")
    if not raw:
        print("RSS narration skipped: publisher supplied no readable story text.")
        return ""

    boilerplate = (
        "read more", "click here", "subscribe to", "sign up for",
        "listen to the podcast", "watch live", "get the latest",
        "download the app", "follow us on"
    )

    # Feeds often encode several sentences without punctuation; use both
    # sentence boundaries and the RSS body itself as an intact source passage.
    raw_parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“\"'])", raw)
    candidates = []
    seen = set()
    for part in raw_parts:
        part = re.sub(r"\s+", " ", part).strip(" -•|")
        if any(x in part.lower() for x in boilerplate):
            continue
        normalized = re.sub(r"[^a-z0-9 ]", "", part.lower()).strip()
        count = len(part.split())
        if normalized and 5 <= count <= 95 and normalized not in seen:
            candidates.append(part)
            seen.add(normalized)

    # If the feed supplied one coherent unpunctuated paragraph, keep that
    # passage as source material rather than losing it during sentence parsing.
    if not candidates and len(raw.split()) >= 12:
        candidates = [raw[:1800]]

    if not candidates:
        print("RSS narration skipped: no usable publisher text.")
        return ""

    action_terms = (
        "announced", "approved", "rejected", "won", "lost", "filed",
        "ruled", "voted", "launched", "reported", "confirmed", "said",
        "plans", "reached", "began", "ended", "ordered", "passed",
        "warned", "expects", "agreed", "will"
    )

    def score(text):
        words = text.split()
        low = text.lower()
        points = 0
        if 8 <= len(words) <= 28:
            points += 3
        if re.search(r"\d", text):
            points += 2
        if any(term in low for term in action_terms):
            points += 2
        if re.search(r"\b[A-Z][a-z]{2,}\b", text):
            points += 1
        if len(words) > 42:
            points -= 1
        return points

    ranked = sorted(enumerate(candidates), key=lambda x: (score(x[1]), -x[0]), reverse=True)
    hook_index, hook = ranked[0]

    # Build approximately 35–55 spoken words from source passages.
    chosen = [hook]
    total_words = len(hook.split())
    for idx, passage in enumerate(candidates):
        if idx == hook_index:
            continue
        if total_words >= 40:
            break
        if total_words + len(passage.split()) <= 58:
            chosen.append(passage)
            total_words += len(passage.split())

    body = " ".join(chosen).strip()
    body_words = body.split()
    if len(body_words) < 20:
        # A single sourced passage can still make a short explainer; do not
        # pad it with invented context just to hit an arbitrary word count.
        if len(raw.split()) >= 20:
            body = " ".join(raw.split()[:58])
        else:
            print("RSS narration skipped: source does not contain enough facts.")
            return ""

    # Keep a short direct hook, then let the source establish the context.
    body = re.sub(r"\s+", " ", body).strip()
    if not body.endswith((".", "!", "?")):
        body += "."
    body += " What do you think about this development?"
    print("Source-grounded hook:", hook[:130], "| narration words:", len(body.split()))
    return body


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
HF_AI_DISABLED = False
HF_AI_ATTEMPTED = False
OPENVERSE_RATE_LIMITED = False
COMMONS_RATE_LIMITED = False

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


def request_image_search_with_backoff(url, params, headers, timeout=25):
    """Retry temporary image-search gateway failures without bypassing provider limits."""
    last_response = None
    for attempt in range(3):
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        last_response = response
        if response.status_code not in (502, 503, 504):
            return response
        if attempt < 2:
            delay = 2 ** (attempt + 1)
            print(f"Image provider temporary HTTP {response.status_code}; retrying in {delay}s.")
            time.sleep(delay)
    return last_response


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
    global OPENVERSE_RATE_LIMITED
    if OPENVERSE_RATE_LIMITED:
        print("Openverse skipped: rate limit already reached in this run.")
        return None
    api = "https://api.openverse.org/v1/images/"
    headers = {"User-Agent": "DigitalOrbitNewsBot/1.4 (https://github.com/sahebchatterjee650-sudo/USA_News_Automation)"}

    for query in image_search_queries(headline, summary):
        params = {
            "q": query,
            "license": "cc0,pdm,by,by-sa",
            "page_size": 8,
            "mature": "false",
            "filter_dead": "true",
        }
        try:
            response = request_image_search_with_backoff(api, params, headers, timeout=25)
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
            message = str(error)
            print("Openverse search failed for query:", query, message[:180])
            if "429" in message or "too many requests" in message.lower():
                OPENVERSE_RATE_LIMITED = True
                print("Openverse rate limit reached; disabling further Openverse requests for this run.")

    return None


def download_commons_image(headline, summary, index):
    """Fallback search on Wikimedia Commons; keep license and creator attribution."""
    global COMMONS_RATE_LIMITED
    if COMMONS_RATE_LIMITED:
        print("Wikimedia Commons skipped: rate limit already reached in this run.")
        return None
    api = "https://commons.wikimedia.org/w/api.php"
    headers = {"User-Agent": "DigitalOrbitNewsBot/1.4 (https://github.com/sahebchatterjee650-sudo/USA_News_Automation)"}
    allowed = ("cc0", "public domain", "cc by", "cc-by", "cc by-sa", "cc-by-sa")
    blocked = ("noncommercial", "nc-", "nd-", "no derivatives")

    for query in image_search_queries(headline, summary):
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,
            "gsrlimit": 8,
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
            message = str(error)
            print("Commons search failed for query:", query, message[:180])
            if "429" in message or "too many requests" in message.lower():
                COMMONS_RATE_LIMITED = True
                print("Commons rate limit reached; disabling further Commons requests for this run.")

    return None


def generate_free_ai_news_image(headline, scene_text, index, visual_no=0):
    """
    Generate a photorealistic contextual scene through the public
    mrfakename/Z-Image-Turbo Hugging Face ZeroGPU Space.
    Free quota is limited and shared; HF_TOKEN can improve authenticated
    access when the user adds a read/inference-enabled token as a GitHub Secret.
    """
    global HF_AI_DISABLED, HF_AI_ATTEMPTED
    # Public free ZeroGPU quota is shared. Limit each run to one attempt;
    # all remaining stories use attractive local designed artwork.
    if HF_AI_DISABLED or HF_AI_ATTEMPTED:
        return None
    if os.getenv("ENABLE_HF_IMAGE_GENERATION", "true").lower() not in ("1", "true", "yes"):
        return None
    HF_AI_ATTEMPTED = True
    scene = re.sub(r"\s+", " ", f"{headline}. {scene_text}").strip()
    prompt = (
        "Photorealistic editorial news photography for a professional US news "
        "briefing. Show this scene: " + scene[:650] + ". "
        "Use the visual language of a real press photograph: natural lighting, "
        "authentic locations, realistic people and objects, documentary camera, "
        "credible proportions, subtle cinematic contrast, detailed textures. "
        "Vertical 9:16 composition with the main subject centered and visible. "
        "Absolutely no text, letters, numbers, typography, documents, paper sheets, "
        "flyers, posters, newspapers, signs, screens full of writing, logos, "
        "watermarks, graphic icons, vector art, cartoon, illustration, diagram, "
        "or infographic. Show a real-world photographic scene, not a designed graphic. "
        "For political or breaking-news stories, create a contextual editorial "
        "scene and do not fabricate a specific event, quote, or identifiable "
        "real person's likeness."
    )
    try:
        from gradio_client import Client
        token = os.getenv("HF_TOKEN", "").strip() or None
        client = Client("mrfakename/Z-Image-Turbo", token=token, verbose=False)
        result = client.predict(
            prompt=prompt,
            height=1344,
            width=768,
            num_inference_steps=9,
            seed=20261122 + index * 31 + visual_no,
            randomize_seed=False,
            api_name="/generate_image",
        )
        generated = result[0] if isinstance(result, (tuple, list)) else result
        if isinstance(generated, dict):
            generated = generated.get("path") or generated.get("url")
        if not generated:
            raise ValueError("Hugging Face Space returned no image")
        if isinstance(generated, Image.Image):
            image = generated.convert("RGB")
        else:
            image = Image.open(generated).convert("RGB")
        if image.width < 512 or image.height < 768:
            raise ValueError(f"Generated image resolution too small: {image.size}")
        path = VIDEO_DIR / f"ai_visual_{index:02}_{visual_no:02}.jpg"
        image = ImageOps.fit(
            image, (768, 1365), method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.45)
        )
        image.save(path, "JPEG", quality=91, optimize=True)
        print("AI-GENERATED PHOTOREALISTIC VISUAL CREATED via Hugging Face ZeroGPU:", path)
        return path
    except Exception as error:
        message = str(error)
        print("Hugging Face free AI image generation unavailable:", message[:260])
        if any(term in message.lower() for term in ("zerogpu", "quota", "exceeded", "rate limit", "too many requests")):
            HF_AI_DISABLED = True
            print("Hugging Face AI disabled for the rest of this workflow run to avoid repeated quota failures.")
        return None



def create_community_quiz_assets(headline, script, source_url, index, keys):
    """Create a source-grounded YouTube Community quiz image and ready-to-paste caption."""
    prompt = f"""
Create one concise multiple-choice quiz about the news item below.
Use ONLY facts explicitly stated in the supplied script. If no safe factual question
can be formed, return exactly SKIP_QUIZ.
Return valid JSON only, with keys: question, options, correct, explanation.
question: one short question; options: exactly three short strings;
correct: exact option text from options; explanation: one sentence.
Do not invent details, dates, statistics, or implications.

HEADLINE: {headline}
SCRIPT: {script}
SOURCE: {source_url}
"""
    raw = generate_script(keys, prompt).strip()
    try:
        cleaned = re.sub(r"^\s*```(?:json)?|\```\s*$", "", raw, flags=re.I).strip()
        data = json.loads(cleaned)
        options = data.get("options", [])
        question = str(data.get("question", "")).strip()
        correct = str(data.get("correct", "")).strip()
        if (len(options) != 3 or not question or correct not in options
                or any(not isinstance(x, str) or not x.strip() for x in options)):
            raise ValueError("Quiz fields failed validation")
    except Exception as error:
        print("Community quiz skipped: no valid source-grounded quiz:", str(error)[:180])
        return None

    canvas = Image.new("RGB", (1080, 1350), (7, 19, 43))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((48, 48, 1032, 1302), radius=42, fill=(12, 34, 65),
                           outline=(27, 170, 225), width=5)
    draw.text((92, 105), "DIGITAL ORBIT  |  NEWS QUIZ", font=get_font(42, True),
              fill=(40, 190, 245))
    draw.text((92, 205), "CAN YOU GET IT RIGHT?", font=get_font(54, True), fill="white")
    y = 355
    for line in textwrap.wrap(question, width=28):
        draw.text((92, y), line, font=get_font(46, True), fill="white")
        y += 64
    y += 45
    for letter, option in zip(("A", "B", "C"), options):
        draw.rounded_rectangle((92, y, 988, y + 145), radius=24,
                               fill=(22, 54, 91), outline=(70, 112, 155), width=3)
        draw.ellipse((116, y + 34, 194, y + 112), fill=(190, 22, 45))
        draw.text((140, y + 47), letter, font=get_font(40, True), fill="white")
        wrapped = textwrap.wrap(str(option), width=30)[:2]
        oy = y + (35 if len(wrapped) == 1 else 20)
        for line in wrapped:
            draw.text((225, oy), line, font=get_font(34, True), fill="white")
            oy += 43
        y += 175
    draw.text((92, 1120), "COMMENT A, B OR C BELOW", font=get_font(43, True),
              fill=(255, 205, 70))
    draw.text((92, 1190), "Answer reveal in a follow-up update", font=get_font(28),
              fill=(210, 225, 240))
    image_path = VIDEO_DIR / f"community_quiz_{index:02}.jpg"
    canvas.save(image_path, "JPEG", quality=94, optimize=True)
    caption = (
        f"\nNEWS QUIZ: {question}\n"
        + "\n".join(f"{letter}. {option}" for letter, option in zip(("A", "B", "C"), options))
        + "\n\nComment A, B, or C. We’ll share the answer in a follow-up.\n"
        + "#DigitalOrbit #USNews #NewsQuiz"
    )
    (VIDEO_DIR / f"community_quiz_{index:02}.txt").write_text(
        caption + f"\n\nSOURCE: {source_url}\n\nINTERNAL ANSWER: {correct}\n{data.get('explanation','')}",
        encoding="utf-8"
    )
    print("COMMUNITY QUIZ IMAGE CREATED:", image_path)
    print("COMMUNITY QUIZ CAPTION CREATED:", VIDEO_DIR / f"community_quiz_{index:02}.txt")
    return image_path


def create_daily_top3_assets(stories):
    """Create a source-linked daily Top 3 text brief from stories actually produced."""
    if len(stories) < 3:
        print("Daily Top 3 skipped: fewer than three eligible stories were produced.")
        return
    lines = ["DIGITAL ORBIT — DAILY TOP 3", ""]
    for number, (headline, url) in enumerate(stories[:3], 1):
        lines.extend([f"{number}. {headline}", f"Source: {url}", ""])
    lines.append("Follow DigitalOrbit for concise, source-linked US news updates.")
    path = VIDEO_DIR / "daily_top_3.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    print("DAILY TOP 3 BRIEF CREATED:", path)


def create_story_illustration(headline, summary, index):
    """
    Zero-cost local fallback illustration. This is an original designed graphic,
    not a documentary photograph or an AI-generated photo.
    """
    # Use the headline as the primary subject; RSS summaries often contain unrelated boilerplate.
    text = (headline or "").lower()
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
    elif any(k in text for k in ("black friday", "cyber monday", "discount", "shopping", "retail", "consumer", "sale", "sales", "price", "prices", "market", "stock", "economy", "inflation", "jobs", "business", "earnings")):
        label = "BUSINESS & ECONOMY"
        # Original retail/consumer illustration, not a government-building graphic.
        draw.rectangle((245, 355, 955, 655), fill=(22, 67, 94), outline=(70, 205, 225), width=8)
        draw.polygon([(210, 355), (600, 190), (990, 355)], fill=(190, 215, 230))
        draw.rectangle((300, 430, 900, 655), fill=(34, 92, 120))
        for x in (365, 520, 675):
            draw.rectangle((x, 455, x+105, 555), fill=(185, 220, 235))
        draw.rounded_rectangle((760, 230, 970, 325), radius=18, fill=(185, 25, 43))
        draw.text((865, 277), "% OFF", font=get_font(42, True), fill="white", anchor="mm")
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
    """
    Prefer a photorealistic contextual AI visual from the free Hugging Face Space.
    If the public Space is unavailable, fall back to an original local editorial
    illustration. Never portray an invented scene as documentary evidence.
    """
    try:
        ai_image = generate_free_ai_news_image(
            headline, summary, index, visual_no=0
        )
        if ai_image and Path(ai_image).exists():
            print("Using photorealistic AI visual for story:", index)
            return Path(ai_image)
    except Exception as error:
        print("AI visual unavailable; using local artwork:", str(error)[:200])

    try:
        return create_story_illustration(headline, summary, index)
    except Exception as error:
        print("Local illustration fallback failed:", str(error)[:240])
        return None


def save_image_attributions():
    if IMAGE_ATTRIBUTIONS:
        (VIDEO_DIR / "image_attributions.txt").write_text(
            "\n".join(IMAGE_ATTRIBUTIONS),
            encoding="utf-8"
        )



# =========================================================
# DIGITAL ORBIT VIDEO BACKGROUND
# =========================================================

def get_story_category(headline):
    from news_quality_gate import classify_category
    return classify_category(headline)


def get_category_accent(category):
    palette = {
        "TECH & AI": (42, 190, 255),
        "US POLITICS": (255, 75, 95),
        "BUSINESS & ECONOMY": (65, 220, 165),
        "PUBLIC SAFETY": (255, 155, 55),
        "WORLD NEWS": (170, 135, 255),
        "WEATHER & CLIMATE": (70, 190, 235),
        "SPORTS": (255, 195, 65),
        "TRAVEL & TRANSPORT": (65, 205, 220),
        "TOP NEWS": (45, 190, 235),
    }
    return palette.get(category, palette["TOP NEWS"])


def draw_wrapped_text(draw, text, font, max_width, max_lines=4):
    """Wrap headline text without allowing a single long token to clip the cover."""
    words = (text or "").upper().split()
    lines, current = [], ""

    def split_oversized_word(word):
        parts = []
        remaining = word
        while remaining and draw.textbbox((0, 0), remaining, font=font)[2] > max_width:
            cut = len(remaining)
            while cut > 1 and draw.textbbox((0, 0), remaining[:cut], font=font)[2] > max_width:
                cut -= 1
            parts.append(remaining[:cut])
            remaining = remaining[cut:]
        if remaining:
            parts.append(remaining)
        return parts

    for original in words:
        for word in split_oversized_word(original):
            candidate = (current + " " + word).strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
    if current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + "...", font=font)[2] > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip(" ,.-") + "..."
    return lines


def create_background(headline, index, news_image=None, output_path=None):
    """Create a polished vertical news scene with category-specific styling."""
    category = get_story_category(headline)
    accent = get_category_accent(category)
    background = Image.new("RGB", (WIDTH, HEIGHT), (5, 12, 27))
    draw = ImageDraw.Draw(background)

    # Deep broadcast gradient; category accent changes the visual identity
    # so every topic does not look like the same generic blue news card.
    for y in range(HEIGHT):
        blend = y / HEIGHT
        color = (
            int(4 + accent[0] * 0.035 * blend),
            int(10 + accent[1] * 0.045 * blend),
            int(25 + accent[2] * 0.10 * blend),
        )
        draw.line((0, y, WIDTH, y), fill=color)

    # Top identity bar.
    draw.rectangle((0, 0, WIDTH, 225), fill=(3, 8, 19))
    draw.rectangle((0, 220, WIDTH, 229), fill=accent)
    logo = load_local_image(LOGO_FILE)
    title_x = 48
    if logo is not None:
        logo.thumbnail((128, 128), Image.Resampling.LANCZOS)
        background.paste(logo, (42, 42))
        title_x = 195
    draw = ImageDraw.Draw(background)
    draw.text((title_x, 43), "DIGITAL ORBIT", font=get_font(55, True),
              fill=(250, 252, 255))
    draw.text((title_x, 118), "USA NEWS  /  EXPLAINED", font=get_font(29, True),
              fill=accent)

    # Topic badge and story number give the layout a stronger editorial hierarchy.
    draw.rounded_rectangle((48, 265, 490, 330), radius=18, fill=accent)
    draw.text((72, 280), category, font=get_font(27, True), fill=(5, 12, 24))
    draw.rounded_rectangle((820, 265, 1032, 330), radius=18, fill=(180, 28, 48))
    draw.text((926, 297), "TOP STORY", font=get_font(24, True),
              fill="white", anchor="mm")

    # A distinct, large hero frame. If an image is unavailable, retain a
    # designed illustration panel rather than leaving a blank placeholder.
    draw.rounded_rectangle((38, 365, 1042, 1285), radius=32,
                           fill=(10, 24, 43), outline=accent, width=5)
    if news_image is not None and Path(news_image).exists():
        try:
            photo = Image.open(news_image).convert("RGB")
            photo = ImageOps.fit(photo, (960, 880),
                                 method=Image.Resampling.LANCZOS,
                                 centering=(0.5, 0.42))
            background.paste(photo, (60, 390))
        except Exception as error:
            print("Could not place story image:", str(error)[:180])
            draw = ImageDraw.Draw(background)
            draw.text((540, 820), category, font=get_font(48, True),
                      fill=accent, anchor="mm")
    else:
        # A clean category-colored editorial fallback.
        draw.ellipse((220, 510, 860, 1150), outline=accent, width=18)
        draw.ellipse((330, 620, 750, 1040), fill=(13, 42, 70))
        draw.text((540, 820), category, font=get_font(44, True),
                  fill=(240, 248, 255), anchor="mm")

    draw = ImageDraw.Draw(background)
    # Add a dark headline panel at the foot of the image for legibility.
    draw.rounded_rectangle((60, 930, 1020, 1255), radius=24,
                           fill=(3, 11, 25), outline=accent, width=3)
    draw.rectangle((60, 930, 1020, 943), fill=accent)
    # Fit the complete headline to the visible panel instead of relying on a
    # fixed font size that can clip unusually long names or multi-part headlines.
    title_font = get_font(48, True)
    lines = draw_wrapped_text(draw, headline, title_font, 870, 4)
    for size in (46, 44, 42, 40, 38, 36, 34):
        line_height = int(size * 1.32)
        if len(lines) * line_height <= 260:
            break
        title_font = get_font(size, True)
        lines = draw_wrapped_text(draw, headline, title_font, 870, 4)
    line_gap = int(title_font.size * 1.32)
    y = 960
    for line in lines:
        box = draw.textbbox((0, 0), line, font=title_font)
        if box[2] > 870:
            raise RuntimeError("Thumbnail headline exceeds safe text width.")
        draw.text((95, y), line, font=title_font,
                  fill=(250, 252, 255))
        y += line_gap

    # Caption-safe lower third and clear call-to-action footer.
    draw.rectangle((0, 1320, WIDTH, 1430), fill=(5, 12, 26))
    draw.rectangle((0, 1320, WIDTH, 1328), fill=accent)
    draw.rounded_rectangle((48, 1350, 520, 1410), radius=16,
                           fill=(180, 28, 48))
    draw.text((284, 1380), "WHAT YOU NEED TO KNOW",
              font=get_font(25, True), fill="white", anchor="mm")
    draw.text((540, 1500), "CLEAR FACTS. IMPORTANT STORIES.",
              font=get_font(29, True), fill=(225, 235, 248), anchor="mm")
    draw.text((540, 1580), "FOLLOW DIGITAL ORBIT",
              font=get_font(34, True), fill=accent, anchor="mm")
    draw.text((540, 1740), "INDEPENDENT NEWS BRIEFING",
              font=get_font(23, True), fill=(170, 190, 210), anchor="mm")

    output = Path(output_path) if output_path else VIDEO_DIR / f"background_{index:02}.png"
    background.save(output)
    return output




# =========================================================
# VOICE GENERATION
# =========================================================

async def create_voice(script, path, voice):
    """Use Edge TTS first, then local eSpeak if the network TTS is unavailable."""
    last_error = None
    for attempt in range(2):
        try:
            if path.exists():
                path.unlink()
            communicator = edge_tts.Communicate(script, voice, rate="-12%")
            await asyncio.wait_for(communicator.save(str(path)), timeout=120)
            if path.exists() and path.stat().st_size > 5000:
                return
            raise RuntimeError("Edge TTS returned an empty audio file.")
        except Exception as error:
            last_error = error
            print(f"Edge TTS attempt {attempt + 1}/2 failed:", str(error)[:240])
            if path.exists():
                path.unlink()
            if attempt == 0:
                await asyncio.sleep(2)
    engine = shutil.which("espeak-ng") or shutil.which("espeak")
    if engine:
        wav_path = path.with_suffix(".wav")
        try:
            subprocess.run([engine, "-v", "en-us", "-s", "125", "-w", str(wav_path), script],
                           check=True, capture_output=True, text=True, timeout=90)
            subprocess.run(["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame",
                            "-b:a", "128k", str(path)],
                           check=True, capture_output=True, text=True, timeout=90)
            if path.exists() and path.stat().st_size > 5000:
                print("VOICE CREATED using local eSpeak fallback.")
                return
        except Exception as fallback_error:
            print("Local eSpeak fallback failed:", str(fallback_error)[:300])
        finally:
            wav_path.unlink(missing_ok=True)
    raise RuntimeError(f"All TTS methods failed. Edge TTS: {last_error}")


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

    # Three-word caption beats make the opening hook easier to read on phones.
    chunks = [
        words[i:i + 3]
        for i in range(0, len(words), 3)
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
# STORY VISUALS AND SHORTS COVER
# =========================================================

def split_script_for_visuals(script, max_segments=3):
    """Split narration into multiple balanced visual beats, even if RSS fallback has few sentences."""
    text = re.sub(r"\s+", " ", script or "").strip()
    if not text:
        return ["USA news update"]

    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
    # Prefer sentence boundaries, but RSS fallback sometimes supplies one long sentence.
    if len(sentences) >= 2:
        count = min(max_segments, len(sentences))
        groups = [[] for _ in range(count)]
        for i, sentence in enumerate(sentences):
            groups[min(count - 1, i * count // len(sentences))].append(sentence)
        segments = [" ".join(group) for group in groups if group]
    else:
        words = text.split()
        count = min(max_segments, max(1, (len(words) + 11) // 12))
        chunk_size = max(1, (len(words) + count - 1) // count)
        segments = [" ".join(words[i:i + chunk_size])
                    for i in range(0, len(words), chunk_size)]

    # For normal 15–60 second Shorts, aim for at least three scene beats.
    words = text.split()
    if len(segments) < 3 and len(words) >= 18:
        count = min(max_segments, 3)
        chunk_size = max(1, (len(words) + count - 1) // count)
        segments = [" ".join(words[i:i + chunk_size])
                    for i in range(0, len(words), chunk_size)]
    return segments[:max_segments] or [text]


def download_visual_for_segment(headline, segment, index, visual_no):
    global OPENVERSE_RATE_LIMITED
    if OPENVERSE_RATE_LIMITED:
        print("Openverse segment search skipped: this run has already hit its rate limit.")
        return None
    query = clean_image_query(headline + " " + segment, "")
    api = "https://api.openverse.org/v1/images/"
    headers = {"User-Agent": "DigitalOrbitNewsBot/1.4 (https://github.com/sahebchatterjee650-sudo/USA_News_Automation)"}
    params = {"q": query, "license": "cc0,pdm,by,by-sa", "page_size": 20,
              "mature": "false", "filter_dead": "true"}
    try:
        response = request_image_search_with_backoff(api, params, headers, timeout=20)
        response.raise_for_status()
        for item in response.json().get("results", []):
            license_code = (item.get("license") or "").lower()
            if license_code not in {"cc0", "pdm", "by", "by-sa"}:
                continue
            image_url = item.get("url") or item.get("thumbnail")
            if not image_url:
                continue
            try:
                image_response = requests.get(image_url, headers=headers, timeout=20)
                image_response.raise_for_status()
                image = Image.open(BytesIO(image_response.content)).convert("RGB")
                if image.width < 500 or image.height < 300:
                    continue
                path = VIDEO_DIR / f"story_{index:02}_visual_{visual_no:02}.jpg"
                image.save(path, "JPEG", quality=90)
                title = html.unescape(item.get("title") or "Untitled image")
                creator = html.unescape(item.get("creator") or "Creator not listed")
                IMAGE_ATTRIBUTIONS.append(
                    f"{title} | {creator} | CC {license_code.upper()} | "
                    f"{item.get('license_url') or ''} | {item.get('foreign_landing_url') or ''}"
                )
                print("Licensed story visual selected:", visual_no, title)
                return path
            except Exception as image_error:
                print("Visual candidate rejected:", str(image_error)[:140])
    except Exception as error:
        message = str(error)
        print("Story visual search unavailable:", message[:160])
        if "429" in message or "too many requests" in message.lower():
            OPENVERSE_RATE_LIMITED = True
            print("Openverse rate limit reached; disabling all further Openverse searches for this run.")
    return None



def download_commons_visual_for_segment(headline, segment, index, visual_no):
    """Search Wikimedia Commons for a real, openly licensed photograph for this scene."""
    global COMMONS_RATE_LIMITED
    if COMMONS_RATE_LIMITED:
        print("Wikimedia Commons segment search skipped: this run has already hit its rate limit.")
        return None
    api = "https://commons.wikimedia.org/w/api.php"
    headers = {"User-Agent": "DigitalOrbitNewsBot/1.4 (https://github.com/sahebchatterjee650-sudo/USA_News_Automation)"}
    query = clean_image_query(headline + " " + segment, "")
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
        "gsrlimit": 20, "prop": "imageinfo", "iiprop": "url|extmetadata",
        "iiurlwidth": 1400
    }
    try:
        response = requests.get(api, params=params, headers=headers, timeout=25)
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
        for page in pages.values():
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            license_name = html.unescape(meta.get("LicenseShortName", {}).get("value", ""))
            normalized = license_name.lower()
            allowed = ("cc0", "public domain", "cc by", "cc-by", "cc by-sa", "cc-by-sa")
            blocked = ("noncommercial", "nc-", "nd-", "no derivatives")
            if not any(term in normalized for term in allowed) or any(term in normalized for term in blocked):
                continue
            image_url = info.get("thumburl") or info.get("url")
            if not image_url:
                continue
            try:
                img_response = requests.get(image_url, headers=headers, timeout=25)
                img_response.raise_for_status()
                image = Image.open(BytesIO(img_response.content)).convert("RGB")
                if image.width < 600 or image.height < 350:
                    continue
                path = VIDEO_DIR / f"story_{index:02}_commons_{visual_no:02}.jpg"
                image.save(path, "JPEG", quality=92)
                title = html.unescape(page.get("title", "Wikimedia Commons image"))
                author = html.unescape(meta.get("Artist", {}).get("value", "Author not listed"))
                license_url = meta.get("LicenseUrl", {}).get("value", "")
                landing = info.get("descriptionurl", "")
                IMAGE_ATTRIBUTIONS.append(
                    f"{title} | {author} | {license_name} | {license_url} | {landing}"
                )
                print("Real Wikimedia Commons scene photo selected:", visual_no, title)
                return path
            except Exception as image_error:
                print("Commons scene candidate rejected:", str(image_error)[:140])
    except Exception as error:
        message = str(error)
        print("Wikimedia Commons scene search failed:", message[:160])
        if "429" in message or "too many requests" in message.lower():
            COMMONS_RATE_LIMITED = True
            print("Wikimedia Commons rate limit reached; disabling further Commons searches for this run.")
    return None


def get_story_visuals(headline, summary, script, index, fallback_image=None):
    """Build a hybrid scene sequence: licensed real photos first, then local art."""
    visuals = []
    if fallback_image and Path(fallback_image).exists():
        visuals.append(Path(fallback_image))

    segments = split_script_for_visuals(script, 3)
    for visual_no, segment in enumerate(segments, 1):
        image_path = None

        # Prefer a relevant, openly licensed real-world image for each scene.
        # Provider functions enforce license filters and attribution.
        try:
            image_path = download_visual_for_segment(
                headline, segment, index, visual_no
            )
        except Exception as error:
            print("Openverse scene lookup failed:", str(error)[:160])

        if not image_path:
            try:
                image_path = download_commons_visual_for_segment(
                    headline, segment, index, visual_no
                )
            except Exception as error:
                print("Wikimedia scene lookup failed:", str(error)[:160])

        # A designed illustration keeps the scene sequence complete when
        # public image providers are unavailable, rate-limited, or irrelevant.
        if not image_path:
            try:
                image_path = create_story_illustration(
                    headline, segment, index * 10 + visual_no
                )
            except Exception as error:
                print("Local scene artwork failed:", str(error)[:180])

        if image_path and Path(image_path).exists():
            visuals.append(Path(image_path))

    unique = []
    hashes = set()
    for path in visuals:
        try:
            digest = hashlib.md5(path.read_bytes()).hexdigest()
        except Exception as error:
            print("Visual file could not be read:", str(error)[:120])
            continue
        if digest not in hashes:
            hashes.add(digest)
            unique.append(path)

    print("Hybrid visual scenes prepared:", len(unique))
    print("Licensed photo attributions collected:", len(IMAGE_ATTRIBUTIONS))
    return unique or ([] if not fallback_image else [Path(fallback_image)])


def create_story_slideshow(headline, index, visuals, duration):
    slide_paths = []
    for visual_no, image_path in enumerate(visuals, 1):
        slide_path = VIDEO_DIR / f"slide_{index:02}_{visual_no:02}.png"
        create_background(headline, index, image_path, output_path=slide_path)
        slide_paths.append(slide_path)
    concat_path = VIDEO_DIR / f"slideshow_{index:02}.txt"
    per_slide = duration / max(1, len(slide_paths))
    lines = []
    for slide_path in slide_paths:
        safe = slide_path.resolve().as_posix().replace("'", "'\\''")
        lines.extend([f"file '{safe}'", f"duration {per_slide:.3f}"])
    safe_last = slide_paths[-1].resolve().as_posix().replace("'", "'\\''")
    lines.append(f"file '{safe_last}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return concat_path


def create_shorts_thumbnail(headline, index, hero_image):
    cover_path = VIDEO_DIR / f"thumbnail_{index:02}.jpg"
    cover_source = create_background(
        headline, index, hero_image,
        output_path=VIDEO_DIR / f"thumbnail_source_{index:02}.png"
    )
    Image.open(cover_source).convert("RGB").save(
        cover_path, "JPEG", quality=94, optimize=True
    )
    print("SHORTS THUMBNAIL CREATED:", cover_path)
    return cover_path


# =========================================================
# MAKE VIDEO
# =========================================================

def make_video(headline, script, index, news_image=None, summary=""):
    audio_path = VIDEO_DIR / f"voice_{index:02}.mp3"
    ass_path = VIDEO_DIR / f"captions_{index:02}.ass"
    video_path = VIDEO_DIR / f"news_{index:02}.mp4"
    voice = get_voice(index)
    print("Selected voice:", voice)

    asyncio.run(create_voice(script, audio_path, voice))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        check=True, capture_output=True, text=True, timeout=30
    )
    duration = float(probe.stdout.strip())
    if duration < 12 or duration > 60:
        print("Video skipped: narration duration outside 12-60 seconds:", duration)
        return False
    if not create_ass(script, duration, ass_path):
        return False

    visuals = get_story_visuals(headline, summary, script, index, news_image)
    if not visuals:
        print("VIDEO SKIPPED: no acceptable visual scenes were available.")
        return False
    thumbnail_path = create_shorts_thumbnail(headline, index, visuals[0])
    concat_path = create_story_slideshow(headline, index, visuals, duration)
    print("Story slideshow slides:", len(visuals), "Thumbnail:", thumbnail_path)

    ass = filter_path(ass_path)
    video_filter = (
        "fps=25,scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,"
        "eq=contrast=1.045:saturation=1.08:brightness=-0.008,"
        "unsharp=5:5:0.35:3:3:0.15,vignette=PI/5,"
        f"ass=filename='{ass}':fontsdir='/usr/share/fonts/truetype/dejavu',"
        "fade=t=in:st=0:d=0.35,"
        f"fade=t=out:st={max(0, duration - 0.45):.3f}:d=0.45"
    )
    lower_third_filter = (
        "drawbox=x='-w+min(t/0.45\\,1)*w':y=1310:w=1080:h=110:"
        "color=0xBE162D@1:t=fill,"
        "drawtext=text='WHAT YOU NEED TO KNOW':"
        "fontfile='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf':"
        "fontsize=39:fontcolor=white:"
        "x='-tw+min(t/0.45\\,1)*135':y=1340,"
        "drawbox=x=0:y=230:w='min(t/0.8\\,1)*1080':h=7:"
        "color=0x19AAE1@1:t=fill"
    )
    ticker_path = VIDEO_DIR / f"ticker_{index:02}.txt"
    ticker_path.write_text(
        f"BREAKING NEWS  •  {headline.upper()}  •  FOLLOW DIGITAL ORBIT FOR UPDATES",
        encoding="utf-8"
    )
    ticker_file = filter_path(ticker_path)
    ticker_filter = (
        f"drawtext=textfile='{ticker_file}':reload=0:"
        "fontfile='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf':"
        "fontsize=30:fontcolor=white:box=1:boxcolor=0x07172D@1:"
        "boxborderw=18:x='w-mod(t*170\\,w+tw)':y=1740"
    )
    video_filter += "," + lower_third_filter + "," + ticker_filter
    audio_filter = "highpass=f=75,loudnorm=I=-16:TP=-1.5:LRA=11"

    try:
        run_command([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-i", str(audio_path), "-vf", video_filter, "-af", audio_filter,
            "-t", str(duration), "-c:v", "libx264", "-preset", "medium", "-crf", "21",
            "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-movflags", "+faststart", str(video_path)
        ])
    except subprocess.CalledProcessError as error:
        print("FFmpeg slideshow video error:")
        if error.stderr:
            print(error.stderr[-4000:])
        raise

    if not video_path.exists() or video_path.stat().st_size < 100000:
        raise RuntimeError("Slideshow video validation failed.")
    print("VIDEO CREATED:", video_path)
    print("THUMBNAIL FILE:", thumbnail_path)
    return True


# =========================================================
# MAIN PROGRAM
# =========================================================

def passes_fast_clear_reliable_gate(entry, headline, link, summary):
    """Mandatory editorial gate: timely, understandable, source-traceable news."""
    from datetime import datetime, timezone
    from urllib.parse import urlparse

    # FAST: reject stale/future-dated items when the publisher supplies a timestamp.
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        try:
            published_at = datetime.fromtimestamp(time.mktime(published), tz=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
            if age_hours < -2 or age_hours > 48:
                print("RELIABILITY GATE: stale or future-dated story skipped:", headline,
                      "| age hours:", round(age_hours, 1))
                return False
        except Exception as error:
            print("Publication timestamp could not be validated:", str(error)[:120])

    # RELIABLE: require a valid HTTPS publisher link and enough source text.
    try:
        parsed = urlparse(link)
        if parsed.scheme != "https" or not parsed.netloc or "." not in parsed.netloc:
            print("RELIABILITY GATE: missing/invalid HTTPS source URL:", headline)
            return False
    except Exception:
        print("RELIABILITY GATE: source URL parsing failed:", headline)
        return False

    if len(summary.split()) < 40:
        print("CLEAR/RELIABLE GATE: not enough source detail to explain the story:", headline)
        return False

    # CLEAR: a usable headline and a source passage are mandatory. Do not
    # manufacture context or consequences when the publisher has not reported them.
    if len(headline.split()) < 4:
        print("CLEAR GATE: headline lacks enough context:", headline)
        return False

    print("FAST/CLEAR/RELIABLE gate passed:", headline)
    return True


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

    feed_entries = fetch_multi_source_news()
    trend_topics = fetch_google_trends_us()
    feed_entries = rank_stories_by_trend_interest(feed_entries, trend_topics)
    # Rank editorial opportunities using trend overlap, freshness and public impact.
    # This is a prioritization heuristic, not a prediction or guarantee of virality.
    feed_entries = rank_stories_for_growth(feed_entries)
    save_growth_priority_report(feed_entries)

    if not feed_entries:
        raise RuntimeError("No RSS news received from any configured source.")

    scripts = []
    created_stories = []
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

    for entry in feed_entries:

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
        from news_quality_gate import is_near_duplicate

        cross_feed_duplicate = next(
            (old for old in seen_headlines if is_near_duplicate(normalized_headline, old)),
            None
        )

        if (
            not headline
            or not link
            or link in seen
            or not normalized_headline
            or normalized_headline in seen_headlines
            or cross_feed_duplicate
        ):
            continue

        seen.add(link)
        seen_headlines.add(normalized_headline)
        attempted += 1

        # Exact and near-duplicate detection against recent uploads.
        near_match = None
        candidate_tokens = set(normalized_headline.split())
        for old_title in published_titles:
            old_tokens = set(old_title.split())
            overlap = len(candidate_tokens & old_tokens) / max(1, len(candidate_tokens | old_tokens))
            similarity = difflib.SequenceMatcher(None, normalized_headline, old_title).ratio()
            if normalized_headline == old_title or similarity >= 0.84 or overlap >= 0.78:
                near_match = old_title
                break
        if near_match:
            print("DUPLICATE / NEAR-DUPLICATE SKIPPED:", headline, "| matched:", near_match)
            continue

        # Permanent editorial policy: Fast, Clear and Reliable.
        # News must be recent (when timestamp is available), explainable,
        # and traceable to the original HTTPS publisher report.
        if not passes_fast_clear_reliable_gate(entry, headline, link, summary):
            continue

        # Validate source-grounded narration BEFORE spending limited AI image quota.
        script = make_rss_only_script(headline, summary)
        used_rss_fallback = True
        minimum_words = 28

        if (
            not script
            or "SKIP_STORY" in script.upper()
            or len(script.split()) < minimum_words
        ):

            print("Story skipped: insufficient safe script content:", headline)

            continue

        # Generate visuals only for stories that passed factual/script checks.
        next_index = video_count + 1
        news_image = get_news_image(headline, summary, next_index)
        if news_image is None:
            print("STORY SKIPPED: both AI visual and local artwork failed:", headline)
            continue

        try:

            print(
                "Creating video:",
                next_index
            )

            success = make_video(
                headline,
                script,
                next_index,
                news_image,
                summary
            )

            if success:

                # Mandatory local pre-publish gate: never upload a broken or non-Shorts render.
                from news_quality_gate import validate_rendered_assets
                video_asset = VIDEO_DIR / f"news_{next_index:02}.mp4"
                thumbnail_asset = VIDEO_DIR / f"thumbnail_{next_index:02}.jpg"
                captions_asset = VIDEO_DIR / f"captions_{next_index:02}.ass"
                quality_ok, quality_reason = validate_rendered_assets(
                    video_asset, thumbnail_asset, captions_asset
                )
                if not quality_ok:
                    print("PRE-PUBLISH QUALITY GATE FAILED:", headline, "|", quality_reason)
                    try:
                        video_asset.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                print("PRE-PUBLISH QUALITY GATE PASSED:", headline, "|", quality_reason)

                # Optional community extras are excluded from the core video pipeline.
                created_stories.append((headline, link))

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
    create_daily_top3_assets(created_stories)
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
