import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

OUT = Path("growth_reports")
OUT.mkdir(exist_ok=True)
ET = ZoneInfo("America/New_York")
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

CHANNELS = [
    ("ABC News", "ABC News"),
    ("CBS News", "CBS News"),
    ("NBC News", "NBC News"),
    ("CNN", "CNN"),
    ("FOX News", "Fox News"),
    ("PBS NewsHour", "PBS NewsHour"),
]
BLOCK_TERMS = [
    "election", "campaign", "political", "politics", "partisan", "candidate",
    "war", "terror", "shooting", "massacre", "abortion", "immigration raid",
    "racial", "religious", "conspiracy", "graphic", "violence", "controversy",
    "assassination", "hostage", "gunman", "bombing", "riot", "lawsuit",
]
TOPIC_MAP = [
    (["weather", "storm", "hurricane", "flood", "rain", "snow", "heat"], "আবহাওয়া ও প্রাকৃতিক পরিস্থিতি নিয়ে", "weather conditions and their possible impact on local communities"),
    (["science", "research", "study", "space", "nasa"], "বিজ্ঞান ও গবেষণার নতুন তথ্য নিয়ে", "a science or research development and what the findings mean"),
    (["technology", "tech", "artificial intelligence", " ai ", "cyber", "data center"], "প্রযুক্তি ও নতুন উদ্ভাবনের খবর নিয়ে", "a technology development and its potential real-world impact"),
    (["business", "economy", "market", "stock", "jobs", "inflation"], "ব্যবসা ও অর্থনীতির সাম্প্রতিক পরিবর্তন নিয়ে", "an economic or business update and its possible effects"),
    (["health", "hospital", "medical", "disease", "healthcare"], "স্বাস্থ্য ও চিকিৎসা সংক্রান্ত তথ্য নিয়ে", "a health-related update and the information viewers should know"),
    (["education", "school", "college", "university"], "শিক্ষা সংক্রান্ত খবর নিয়ে", "an education update and how it may affect students and families"),
    (["transport", "airport", "airline", "rail", "road"], "পরিবহন ও যাতায়াত সংক্রান্ত খবর নিয়ে", "a transport update and its possible impact on travelers"),
    (["community", "local", "neighborhood", "public safety", "rescue"], "স্থানীয় সম্প্রদায় ও জননিরাপত্তা নিয়ে", "a community update and the practical information residents may need"),
    (["environment", "climate", "wildlife", "pollution"], "পরিবেশ ও প্রকৃতি সংক্রান্ত খবর নিয়ে", "an environmental update and its potential local impact"),
    (["consumer", "recall", "product", "food safety"], "ভোক্তা সুরক্ষা ও পণ্য সংক্রান্ত খবর নিয়ে", "a consumer information update and what customers should check"),
]

def enabled(name):
    return os.getenv("RUN_" + name, "false").lower() == "true"

def youtube_service():
    raw = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if not raw:
        raise RuntimeError("Missing YOUTUBE_TOKEN_JSON GitHub Secret.")
    info = json.loads(raw)
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds.valid:
        raise RuntimeError("YouTube OAuth token is invalid. Refresh/regenerate it locally; never paste it in chat.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)

def write_report(filename, title, sections):
    body = [f"# {title}", "", f"Generated: {datetime.now(timezone.utc).isoformat()}", ""]
    for heading, content in sections:
        body += [f"## {heading}", "", content.strip() or "No data available.", ""]
    (OUT / filename).write_text("\n".join(body), encoding="utf-8")

def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"#\w+", "", value)
    return re.sub(r"\s+", " ", value).strip()

def topic_for(title, description=""):
    from growth_quality import classify_topic
    _, bn, en = classify_topic(clean_text(title), clean_text(description))
    return bn, en

def safe_topic(title, description=""):
    text = clean_text(title + " " + description).lower()
    if any(term in text for term in BLOCK_TERMS):
        return False
    # Reject tag-only, empty, or uninformative search results.
    title_clean = clean_text(title)
    return len(title_clean.split()) >= 4 and not title_clean.startswith("#")

def make_comment(title, description):
    # Topic-specific discussion prompt; this is a human-review draft, never auto-posted.
    from growth_quality import discussion_prompt
    return discussion_prompt(clean_text(title), clean_text(description))

def engagement_research(youtube):
    rows, seen = [], set()
    after = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    for channel_name, query in CHANNELS:
        try:
            # First resolve the publisher's official channel, then search only within it.
            channel_result = youtube.search().list(
                part="snippet", q=query, type="channel", maxResults=5, safeSearch="strict"
            ).execute()
            channel_items = channel_result.get("items", [])
            channel_id = None
            for ch in channel_items:
                sn = ch.get("snippet", {})
                if query.lower() in (sn.get("title", "") + " " + sn.get("description", "")).lower():
                    channel_id = ch.get("id", {}).get("channelId")
                    if channel_id:
                        break
            if not channel_id:
                rows.append(f"### {channel_name}\nOfficial channel could not be resolved confidently; skipped to avoid unrelated results.\n")
                continue
            result = youtube.search().list(
                part="snippet", channelId=channel_id, type="video", order="date",
                publishedAfter=after, maxResults=15, safeSearch="strict"
            ).execute()
            for item in result.get("items", []):
                sn = item.get("snippet", {})
                title, desc = clean_text(sn.get("title", "")), clean_text(sn.get("description", ""))
                vid = item.get("id", {}).get("videoId")
                if not vid or vid in seen or not safe_topic(title, desc):
                    continue
                # Description must contain useful prose; hashtag-only results are rejected.
                if len(desc.split()) < 12:
                    continue
                seen.add(vid)
                bn_topic, en_topic = topic_for(title, desc)
                discussion = make_comment(title, desc)
                rows.append(
                    f"### {title}\n"
                    f"- Channel: {channel_name}\n"
                    f"- Link: https://www.youtube.com/watch?v={vid}\n"
                    f"- বাংলা সংক্ষিপ্ত বিবরণ: {title} — {bn_topic}। ইংরেজি শিরোনাম ও ভিডিওর বর্ণনা থেকে বিষয়টি চিহ্নিত করা হয়েছে; এটি পূর্ণাঙ্গ অনুবাদ নয়।\n"
                    f"- English topic note: {en_topic}.\n"
                    f"- Suggested discussion prompt (human review only; not auto-posted): {discussion}\n"
                )
        except Exception as exc:
            rows.append(f"### {channel_name}\nSearch unavailable: {str(exc)[:220]}\n")
    write_report("01_smart_engagement.md", "Smart Engagement Research", [
        ("নিরাপদ, ইউনিক ভিডিও ও Comment Draft", "\n".join(rows) if rows else "বর্ণনা যথেষ্ট বিস্তারিত এমন নতুন ও নিরাপদ ভিডিও পাওয়া যায়নি।"),
        ("নোট", "একই ভিডিও একবারই রাখা হয়। বিতর্কিত/সংবেদনশীল শব্দ থাকলে বাদ দেওয়া হয়। এগুলি Draft; কোনো Like বা Comment স্বয়ংক্রিয়ভাবে পোস্ট করা হয় না।")
    ])

def get_channel_and_uploads(youtube):
    data = youtube.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
    items = data.get("items", [])
    if not items:
        raise RuntimeError("OAuth token did not identify a YouTube channel.")
    channel = items[0]
    playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    videos, token = [], None
    for _ in range(4):
        page = youtube.playlistItems().list(part="snippet,contentDetails", playlistId=playlist,
                                            maxResults=50, pageToken=token).execute()
        videos += page.get("items", [])
        token = page.get("nextPageToken")
        if not token:
            break
    ids = list(dict.fromkeys(x.get("contentDetails", {}).get("videoId") for x in videos))
    ids = [x for x in ids if x]
    details = []
    for start in range(0, len(ids), 50):
        part = youtube.videos().list(part="snippet,statistics", id=",".join(ids[start:start+50])).execute()
        details += part.get("items", [])
    return channel, details

def analytics_report(channel, videos):
    stats = channel.get("statistics", {})
    views = sum(int(v.get("statistics", {}).get("viewCount", 0)) for v in videos)
    likes = sum(int(v.get("statistics", {}).get("likeCount", 0)) for v in videos)
    comments = sum(int(v.get("statistics", {}).get("commentCount", 0)) for v in videos)
    engagement = ((likes + comments) / views * 100) if views else 0
    lines = [
        f"- Channel: {channel.get('snippet', {}).get('title', 'DigitalOrbit')}",
        f"- Subscribers: {stats.get('subscriberCount', 'hidden/unavailable')}",
        f"- Total channel views: {stats.get('viewCount', 'unavailable')}",
        f"- Total videos: {stats.get('videoCount', 'unavailable')}",
        f"- Recent listed videos' current views: {views}",
        f"- Recent listed videos' likes: {likes}",
        f"- Recent listed videos' comments: {comments}",
        f"- Simple interaction rate (likes + comments / views): {engagement:.2f}%",
        "", "### Recent uploads"
    ]
    for v in sorted(videos, key=lambda x: x.get("snippet", {}).get("publishedAt", ""), reverse=True)[:30]:
        s = v.get("statistics", {})
        lines.append(f"- {v.get('snippet', {}).get('title','Untitled')} | Views: {s.get('viewCount','0')} | Likes: {s.get('likeCount','hidden')} | Comments: {s.get('commentCount','hidden')}")
    lines += ["", "Scope note: channel lifetime totals come from the channel resource; recent listed video totals are the sum of the fetched upload playlist window (up to 200 videos), so these figures are not expected to match.", "This is a simple interaction rate, not YouTube's official engagement metric. Audience retention, impressions, viewed-vs-swiped-away and viewer-online hours require YouTube Analytics API authorization (yt-analytics.readonly)."]
    write_report("02_youtube_analytics.md", "YouTube Analytics Report", [("Channel performance", "\n".join(lines))])

def hook_set(title, description=""):
    from growth_quality import build_hooks_and_titles
    hooks, _ = build_hooks_and_titles(clean_text(title), clean_text(description))
    return hooks

def generate_viral_content(videos):
    sections = []
    for v in videos[:20]:
        sn = v.get("snippet", {})
        title = clean_text(sn.get("title", "USA News Update"))
        from growth_quality import build_hooks_and_titles
        hooks, titles = build_hooks_and_titles(title, clean_text(sn.get("description", "")))
        sections.append(
            f"### Original: {title}\n"
            + "\n".join(f"- Hook {i+1}: {h}" for i, h in enumerate(hooks)) + "\n"
            + "\n".join(f"- Title option {i+1}: {t}" for i, t in enumerate(titles)) + "\n"
            + "- বাংলা নোট: শিরোনামগুলি উৎসের শিরোনাম থেকে তৈরি খসড়া; প্রকাশের আগে তথ্য যাচাই করুন।\n"
        )
    write_report("03_viral_hooks_titles.md", "Viral Hook & Title Generator", [("Topic-specific drafts", "\n".join(sections) or "No recent videos found.")])

def community_drafts(videos):
    body = []
    for v in videos[:5]:
        title = clean_text(v.get("snippet", {}).get("title", ""))
        bn, _ = topic_for(title)
        prompt = make_comment(title, "")
        body.append(
            f"### Topic: {title}\n"
            f"- English post: We’re tracking this update: {title}. {prompt}\n"
            f"- Poll question: What should we clarify next?\n"
            f"- Options: Confirmed facts | Local impact | Official response | What happens next\n"
            f"- বাংলা অর্থ: আমরা এই আপডেটটি অনুসরণ করছি: {title}। পরবর্তী কোন বিষয়টি আমরা ব্যাখ্যা করব? কোন তথ্যটি আপনার সবচেয়ে কাজে লাগবে?\n"
        )
    write_report("04_community_post_drafts.md", "Community Post Generator", [
        ("Topic-specific English drafts and Bengali meanings", "\n".join(body) or "No recent video topics available."),
        ("নোট", "শুধু Draft তৈরি হয়; Community-তে স্বয়ংক্রিয়ভাবে প্রকাশ করা হয় না।")
    ])

def publishing_analyzer(videos):
    buckets = defaultdict(lambda: {"views": 0, "count": 0})
    for v in videos:
        sn = v.get("snippet", {})
        published = sn.get("publishedAt")
        if not published:
            continue
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(ET)
            key = dt.strftime("%A %I %p ET")
            buckets[key]["views"] += int(v.get("statistics", {}).get("viewCount", 0))
            buckets[key]["count"] += 1
        except Exception:
            pass
    qualified = [(k, v) for k, v in buckets.items() if v["count"] >= 5]
    qualified.sort(key=lambda x: x[1]["views"] / x[1]["count"], reverse=True)
    lines = [f"- {k}: average current views {v['views']//v['count']} across {v['count']} videos" for k, v in qualified[:10]]
    if not lines:
        lines = ["এখনও নির্ভরযোগ্য সময়-সুপারিশের জন্য একই সময়স্লটে অন্তত ৫টি ভিডিওর তথ্য নেই। আরও ডেটা সংগ্রহ করা হবে।"]
    lines.append("প্রকাশের সময় ও Views-এর সম্পর্ক কেবল ঐতিহাসিক পর্যবেক্ষণ; এটি বেশি Views-এর নিশ্চয়তা দেয় না।")
    write_report("05_publishing_time_analysis.md", "Best Publishing Time Analyzer", [("Time slots with at least 5 videos (small samples excluded)", "\n".join(lines))])

def weekly_report(videos):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = []
    for v in videos:
        try:
            dt = datetime.fromisoformat(v.get("snippet", {}).get("publishedAt", "").replace("Z", "+00:00"))
            if dt >= cutoff:
                recent.append(v)
        except Exception:
            pass
    views = sum(int(v.get("statistics", {}).get("viewCount", 0)) for v in recent)
    likes = sum(int(v.get("statistics", {}).get("likeCount", 0)) for v in recent)
    comments = sum(int(v.get("statistics", {}).get("commentCount", 0)) for v in recent)
    top = sorted(recent, key=lambda v: int(v.get("statistics", {}).get("viewCount", 0)), reverse=True)[:5]
    top_lines = [f"- {v.get('snippet', {}).get('title', 'Untitled')} — {v.get('statistics', {}).get('viewCount', '0')} views" for v in top]
    write_report("06_weekly_growth_report.md", "Weekly Growth Report", [
        ("Last 7 days", f"- Videos: {len(recent)}\n- Current views on these videos: {views}\n- Likes: {likes}\n- Comments: {comments}"),
        ("Top videos", "\n".join(top_lines) or "No recent uploads in the last 7 days."),
        ("বাংলা পরামর্শ", "যে বিষয়ের ভিডিওতে তুলনামূলক বেশি Views ও দর্শকের প্রতিক্রিয়া এসেছে, পরের সপ্তাহে সেই ধরনের তথ্যভিত্তিক কনটেন্ট পরীক্ষা করুন।")
    ])

def main():
    selected = [x for x in ["SMART_ENGAGEMENT", "YOUTUBE_ANALYTICS", "VIRAL_CONTENT", "COMMUNITY_POSTS", "PUBLISHING_ANALYZER", "WEEKLY_REPORT"] if enabled(x)]
    if not selected:
        print("No Growth Automation module selected; exiting.")
        return
    yt = youtube_service()
    channel = videos = None
    if any(x != "SMART_ENGAGEMENT" for x in selected):
        channel, videos = get_channel_and_uploads(yt)
    if enabled("SMART_ENGAGEMENT"):
        engagement_research(yt)
    if enabled("YOUTUBE_ANALYTICS"):
        analytics_report(channel, videos)
    if enabled("VIRAL_CONTENT"):
        generate_viral_content(videos)
    if enabled("COMMUNITY_POSTS"):
        community_drafts(videos)
    if enabled("PUBLISHING_ANALYZER"):
        publishing_analyzer(videos)
    if enabled("WEEKLY_REPORT"):
        weekly_report(videos)
    print("Growth reports created:", ", ".join(p.name for p in sorted(OUT.glob("*.md"))))

if __name__ == "__main__":
    main()
