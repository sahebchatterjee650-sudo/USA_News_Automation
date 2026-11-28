"""Pre-publish editorial and media checks for Digital Orbit.

All checks are deterministic and offline except ffprobe, which inspects the
locally rendered MP4. No credentials, network calls, or publishing settings.
"""
import json
import re
import subprocess
from pathlib import Path


CATEGORY_RULES = [
    ("WEATHER & CLIMATE", ("thanksgiving storm", "storm", "storms", "hurricane", "weather", "flood", "flooding", "rain", "snow", "heat wave", "tornado", "climate")),
    ("BUSINESS & ECONOMY", ("black friday", "cyber monday", "discount", "discounts", "shopping", "retail", "consumer", "price", "prices", "sale", "sales", "market", "stock", "economy", "inflation", "jobs", "company", "business", "earnings", "tariff")),
    ("US POLITICS", ("election", "midterm", "president", "trump", "biden", "congress", "senate", "democrat", "republican", "governor", "white house", "campaign", "ballot", "voting", "politics")),
    ("LAW & COURTS", ("judge", "court", "lawsuit", "ruling", "supreme court", "legal", "indictment", "prosecutor", "sentencing", "appeal")),
    ("TECH & AI", ("artificial intelligence", "technology", "tech", "cyber", "hacker", "nvidia", "software", "semiconductor", "chipmaker", "data breach")),
    ("PUBLIC SAFETY", ("explosion", "blast", "fire", "police", "shooting", "attack", "emergency", "rescue", "crash", "injured", "killed", "safety")),
    ("TRAVEL & TRANSPORT", ("airport", "airline", "flight", "plane", "rail", "road", "traffic", "travel", "transport", "delays")),
    ("SPORTS", ("nfl", "nba", "mlb", "soccer", "football", "basketball", "baseball", "coach", "quarterback", "playoff", "game", "team")),
    ("WORLD NEWS", ("ukraine", "russia", "iran", "israel", "gaza", "nato", "zelensky", "putin", "warship", "diplomacy", "international")),
]
STOPWORDS = {
    "the", "and", "for", "with", "from", "after", "over", "into", "says", "said",
    "will", "what", "when", "where", "could", "would", "about", "this", "that",
    "have", "been", "were", "they", "their", "news", "latest", "update", "today",
    "live", "video", "how", "why", "are", "was", "its", "his", "her", "has",
    "not", "new", "more", "than", "you", "your", "all", "one", "two", "who",
}


def tokens(value):
    return {
        word for word in re.findall(r"[a-z0-9]{3,}", (value or "").lower())
        if word not in STOPWORDS
    }


def classify_category(headline):
    """Return the best-fit display category from the headline only."""
    text = " " + re.sub(r"[^a-z0-9]+", " ", (headline or "").lower()).strip() + " "
    for label, keywords in CATEGORY_RULES:
        for keyword in keywords:
            normalized = " " + re.sub(r"[^a-z0-9]+", " ", keyword.lower()).strip() + " "
            if normalized in text:
                return label
    return "TOP NEWS"


def trend_relevance(headline, trend):
    """Require meaningful topical overlap; a shared generic place/name is insufficient."""
    h, t = tokens(headline), tokens(trend)
    if not h or not t:
        return 0.0
    if len(t) == 1:
        return 1.0 if t <= h else 0.0
    shared = h & t
    if len(shared) < 2:
        return 0.0
    coverage = len(shared) / len(t)
    jaccard = len(shared) / len(h | t)
    if coverage < 0.5 or jaccard < 0.2:
        return 0.0
    return round((coverage * 0.65) + (jaccard * 0.35), 4)


def is_near_duplicate(headline, other_headline, threshold=0.78):
    """Conservative title similarity check for cross-feed duplicate suppression."""
    a, b = tokens(headline), tokens(other_headline)
    if not a or not b:
        return False
    overlap = len(a & b) / len(a | b)
    if overlap >= threshold:
        return True
    from difflib import SequenceMatcher
    normalize = lambda x: " ".join(sorted(tokens(x)))
    return SequenceMatcher(None, normalize(headline), normalize(other_headline)).ratio() >= 0.88


def validate_rendered_assets(video_path, thumbnail_path, captions_path):
    """Return (ok, reason) after checking MP4 streams, portrait ratio and sidecars."""
    video_path, thumbnail_path, captions_path = map(Path, (video_path, thumbnail_path, captions_path))
    for path, minimum in ((video_path, 100_000), (thumbnail_path, 10_000), (captions_path, 100)):
        if not path.exists() or path.stat().st_size < minimum:
            return False, f"missing or undersized required asset: {path.name}"
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video_path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
    except Exception as exc:
        return False, f"ffprobe could not inspect MP4: {exc}"
    streams = data.get("streams", [])
    video = next((x for x in streams if x.get("codec_type") == "video"), None)
    audio = next((x for x in streams if x.get("codec_type") == "audio"), None)
    if not video or not audio:
        return False, "MP4 must contain both video and audio streams"
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if not width or not height or height <= width:
        return False, f"video is not portrait 9:16: {width}x{height}"
    ratio = width / height
    if abs(ratio - (9 / 16)) > 0.015:
        return False, f"video aspect ratio is not 9:16: {width}x{height}"
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    if not 12 <= duration <= 60:
        return False, f"video duration outside 12-60 seconds: {duration:.2f}"
    return True, f"validated {width}x{height}, {duration:.2f}s, audio/video, thumbnail and captions"



def validate_original_narration(script, source_text, min_words=40, max_words=85):
    """Reject empty, thin, generic, or near-verbatim narration before rendering."""
    text = re.sub(r"\\s+", " ", (script or "")).strip()
    words = re.findall(r"[A-Za-z0-9']+", text)
    if not text or text.upper() == "SKIP_STORY":
        return False, "model declined or returned no narration"
    if not min_words <= len(words) <= max_words:
        return False, f"narration length outside {min_words}-{max_words} words: {len(words)}"
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\\s+", text) if part.strip()]
    if len(sentences) < 3:
        return False, "explainer needs at least three clear spoken beats"
    lowered = text.lower()
    if any(phrase in lowered for phrase in (
        "what do you think", "like and subscribe", "you won't believe",
        "you will not believe", "welcome back", "breaking news"
    )):
        return False, "generic engagement or filler phrase detected"

    source = re.sub(r"\\s+", " ", (source_text or "")).strip().lower()
    script_lower = text.lower()
    def ngrams(value, size=5):
        tokens = re.findall(r"[a-z0-9']+", value)
        return {" ".join(tokens[i:i + size]) for i in range(max(0, len(tokens) - size + 1))}
    script_grams = ngrams(script_lower)
    source_grams = ngrams(source)
    if not script_grams:
        return False, "not enough narration text to compare with source"
    copied_ratio = len(script_grams & source_grams) / len(script_grams)
    if copied_ratio > 0.55:
        return False, f"too close to publisher wording: {copied_ratio:.0%} matching five-word phrases"

    source_tokens = set(re.findall(r"[a-z0-9]{3,}", source))
    script_tokens = set(re.findall(r"[a-z0-9]{3,}", script_lower))
    grounding = len(source_tokens & script_tokens) / max(1, len(script_tokens))
    if grounding < 0.12:
        return False, "too few source-linked terms; factual grounding is unclear"
    return True, f"{len(words)} words, {len(sentences)} sentences, {copied_ratio:.0%} five-word phrase overlap"


def split_visual_segments(script, max_segments=5):
    """Split narration into up to five balanced, source-ordered visual beats."""
    text = re.sub(r"\s+", " ", script or "").strip()
    if not text:
        return ["USA news update"]
    words = text.split()
    count = min(max(1, max_segments), len(words))
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) >= count:
        groups = [[] for _ in range(count)]
        for i, sentence in enumerate(sentences):
            groups[min(count - 1, i * count // len(sentences))].append(sentence)
        segments = [" ".join(group) for group in groups if group]
    else:
        chunk_size = (len(words) + count - 1) // count
        segments = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
    return segments[:max_segments] or [text]


def visual_scene_label(scene_no):
    """Stable, factual-neutral labels for five distinct visual beats."""
    return {
        1: "THE CONTEXT",
        2: "KEY DEVELOPMENT",
        3: "WHO IS AFFECTED",
        4: "WHAT HAPPENS NEXT",
        5: "THE LATEST UPDATE",
    }.get(int(scene_no), "THE STORY")
