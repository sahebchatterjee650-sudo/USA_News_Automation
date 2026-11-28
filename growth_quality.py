"""Deterministic editorial helpers for Digital Orbit growth reports."""
import re

RULES = [
    ("weather", ["weather", "storm", "hurricane", "flood", "rain", "snow", "heat", "power outage"], "আবহাওয়া ও প্রাকৃতিক পরিস্থিতি নিয়ে", "weather and its reported impact on communities"),
    ("geopolitics", ["ukraine", "russia", "zelensky", "putin", "iran", "israel", "gaza", "military", "warship", "drone attack", "nato"], "আন্তর্জাতিক সংঘাত ও ভূরাজনীতি নিয়ে", "international conflict, diplomacy and reported developments"),
    ("politics", ["trump", "biden", "white house", "congress", "senate", "democrat", "democrats", "republican", "republicans", "election", "campaign", "president", "governor", "political", "politics", "vance"], "মার্কিন রাজনীতি ও সরকারি সিদ্ধান্ত নিয়ে", "US politics, public officials and government decisions"),
    ("law", ["judge", "court", "lawsuit", "ruling", "ban", "legal", "indictment", "investigation", "prosecutor"], "আদালত ও আইনি সিদ্ধান্ত নিয়ে", "a court action, legal decision or investigation"),
    ("technology", ["technology", "tech", "artificial intelligence", " ai ", "cyber", "hacker", "data center", "nvidia", "software"], "প্রযুক্তি ও সাইবার নিরাপত্তা নিয়ে", "technology, cybersecurity and practical implications"),
    ("business", ["business", "economy", "market", "stock", "jobs", "inflation", "prices", "retail", "shopping", "black friday"], "ব্যবসা ও অর্থনীতির সাম্প্রতিক পরিবর্তন নিয়ে", "business, markets and consumer or worker impact"),
    ("public_safety", ["explosion", "detonation", "lead", "fire", "police", "safety", "emergency", "outage", "missing", "rescue"], "জননিরাপত্তা ও জরুরি পরিস্থিতি নিয়ে", "public safety, emergency response and confirmed local impact"),
    ("transport", ["airport", "airline", "plane", "flight", "rail", "road", "travel", "parade", "traffic"], "পরিবহন ও যাতায়াত সংক্রান্ত খবর নিয়ে", "transportation, travel disruptions and practical updates"),
    ("sports", ["nfl", "nba", "mlb", "soccer", "football", "game", "team", "coach", "quarterback", "sports"], "খেলাধুলা ও ক্রীড়া সংক্রান্ত খবর নিয়ে", "sports results, teams and developments"),
    ("human_interest", ["dog show", "coonhound", "community", "deaf soccer", "school band", "charity", "volunteer", "family"], "মানুষ ও সমাজের গুরুত্বপূর্ণ ঘটনা নিয়ে", "a human-interest or community story"),
    ("science", ["science", "research", "study", "space", "nasa", "scientist"], "বিজ্ঞান ও গবেষণার নতুন তথ্য নিয়ে", "a science or research development and what findings mean"),
]

def _text(title, description=""):
    value = re.sub(r"https?://\S+", " ", f"{title or ''} {description or ''}".lower())
    return " " + re.sub(r"[^a-z0-9]+", " ", value).strip() + " "

def classify_topic(title, description=""):
    raw = f"{title or ''} {description or ''}".lower()
    normalized = _text(title, description)
    for category, keywords, bn, en in RULES:
        if any((key in raw if " " in key else f" {key} " in normalized) for key in keywords):
            return category, bn, en
    return "general", "যুক্তরাষ্ট্রের সাম্প্রতিক সাধারণ খবর নিয়ে", "a general US news update; verify the linked source for full details"

def discussion_prompt(title, description=""):
    category, _, _ = classify_topic(title, description)
    prompts = {
        "weather": "Which affected area or practical update should we verify next?",
        "geopolitics": "Which confirmed development or diplomatic response should we explain next?",
        "politics": "Which policy detail or official response deserves closer examination?",
        "law": "What part of the ruling or legal process should we explain next?",
        "technology": "Which security or real-world impact should we examine next?",
        "business": "Which verified figure or consumer impact should we break down next?",
        "public_safety": "Which confirmed safety update should viewers know next?",
        "transport": "Which route, airport or passenger impact needs a clearer update?",
        "sports": "Which result or team development should we cover next?",
        "human_interest": "Which part of this community story would you like more context on?",
        "science": "What does the evidence show, and what remains uncertain?",
        "general": "Which confirmed detail would help make this story clearer?",
    }
    return prompts[category]

def _clip_words(value, limit):
    words = (value or "").split()
    if len(words) <= limit:
        return value
    return " ".join(words[:limit]).rstrip(" ,:;-") + "…"

def build_hooks_and_titles(title, description=""):
    headline = (title or "This latest US update").strip().rstrip(".")
    category, _, _ = classify_topic(title, description)
    subject = _clip_words(headline, 12)
    openers = {
        "weather": f"{subject}: what residents need to know right now.",
        "geopolitics": f"{subject}: what is confirmed, and what happens next?",
        "politics": f"{subject}: the decision, the response and what comes next.",
        "law": f"{subject}: what the court actually decided.",
        "technology": f"{subject}: the security and real-world implications.",
        "business": f"{subject}: what the numbers could mean for consumers.",
        "public_safety": f"{subject}: the confirmed facts and safety implications.",
        "transport": f"{subject}: what travelers need to know.",
        "sports": f"{subject}: the result and why it matters.",
        "human_interest": f"{subject}: the story behind the headline.",
        "science": f"{subject}: what the evidence does—and does not—show.",
        "general": f"{subject}: the confirmed facts behind the headline.",
    }
    hooks = [
        openers[category],
        f"What is confirmed about {subject}? Here are the details.",
        f"Before drawing conclusions about {subject}, here is what the source reports.",
    ]
    titles = [
        _clip_words(headline, 13),
        f"What We Know About {_clip_words(headline, 10)}",
        f"{_clip_words(headline, 11)}: What Happens Next",
    ]
    return hooks, titles
