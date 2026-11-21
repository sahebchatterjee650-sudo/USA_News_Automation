import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

OUT = Path("growth_reports")
OUT.mkdir(exist_ok=True)
ET_TZ = "America/New_York"
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

# Keep research focused on mainstream, general-interest US news.
CHANNELS = [
    ("ABC News", "ABC News"),
    ("CBS News", "CBS News"),
    ("NBC News", "NBC News"),
    ("CNN", "CNN"),
    ("FOX News", "Fox News"),
    ("PBS NewsHour", "PBS NewsHour"),
]
SAFE_TERMS = ["weather", "science", "technology", "business", "economy", "education",
              "space", "health", "transportation", "consumer", "community", "environment",
              "infrastructure", "public safety", "research"]
BLOCK_TERMS = ["election", "campaign", "political", "politics", "partisan", "candidate",
               "war", "terror", "shooting", "massacre", "abortion", "immigration raid",
               "racial", "religious", "conspiracy", "graphic", "violence", "controversy"]

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
    (OUT / filename).write_text("\\n".join(body), encoding="utf-8")

def safe_topic(title, description=""):
    text = (title + " " + description).lower()
    if any(term in text for term in BLOCK_TERMS):
        return False
    return any(term in text for term in SAFE_TERMS)

def engagement_research(youtube):
    rows = []
    # YouTube Data API search uses the existing readonly OAuth grant; no API key is embedded.
    for channel_name, query in CHANNELS:
        try:
            result = youtube.search().list(
                part="snippet", q=query, type="video", order="date",
                publishedAfter=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat(),
                maxResults=8, safeSearch="strict"
            ).execute()
            for item in result.get("items", []):
                sn = item.get("snippet", {})
                title = sn.get("title", "")
                desc = sn.get("description", "")
                if not safe_topic(title, desc):
                    continue
                vid = item.get("id", {}).get("videoId")
                if not vid:
                    continue
                # Drafts only. No automated likes, comments, subscriptions or replies.
                comment = ("Thanks for sharing this informative update. "
                           "Clear reporting helps viewers understand how this development may affect everyday life.")
                rows.append(
                    f"### {title}\\n"
                    f"- Channel/search source: {channel_name}\\n"
                    f"- Link: https://www.youtube.com/watch?v={vid}\\n"
                    f"- বাংলা সংক্ষিপ্ত বিবরণ: {desc[:700] or 'ভিডিওর বিবরণ পাওয়া যায়নি; শিরোনামের ভিত্তিতে বিষয়টি চিহ্নিত করা হয়েছে।'}\\n"
                    f"- Suggested English comment (draft only): {comment}\\n"
                    f"- বাংলা অর্থ: এই তথ্যবহুল আপডেটটি শেয়ার করার জন্য ধন্যবাদ। পরিষ্কার প্রতিবেদন দর্শকদের বুঝতে সাহায্য করে যে এই ঘটনাটি দৈনন্দিন জীবনে কী প্রভাব ফেলতে পারে।\\n"
                )
        except Exception as exc:
            rows.append(f"### {channel_name}\\nSearch unavailable: {str(exc)[:250]}\\n")
    write_report("01_smart_engagement.md", "Smart Engagement Research", [
        ("নিরাপদ ভিডিও ও Comment Draft", "\\n".join(rows) if rows else "নিরাপদ ফিল্টার পেরিয়ে কোনো ভিডিও পাওয়া যায়নি।"),
        ("নোট", "এগুলি পর্যালোচনার জন্য Draft। কোনো Like বা Comment স্বয়ংক্রিয়ভাবে পোস্ট করা হয় না।")
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
    ids = [x.get("contentDetails", {}).get("videoId") for x in videos]
    ids = [x for x in ids if x]
    details = []
    for start in range(0, len(ids), 50):
        part = youtube.videos().list(part="snippet,statistics", id=",".join(ids[start:start+50])).execute()
        details += part.get("items", [])
    return channel, details

def analytics_report(channel, videos):
    stats = channel.get("statistics", {})
    lines = [
        f"- Channel: {channel.get('snippet', {}).get('title', 'DigitalOrbit')}",
        f"- Subscribers: {stats.get('subscriberCount', 'hidden/unavailable')}",
        f"- Total views: {stats.get('viewCount', 'unavailable')}",
        f"- Total videos: {stats.get('videoCount', 'unavailable')}",
        "", "### Recent uploads (up to 200)"
    ]
    for v in videos[:30]:
        s = v.get("statistics", {})
        lines.append(f"- {v.get('snippet', {}).get('title','Untitled')} | Views: {s.get('viewCount','0')} | Likes: {s.get('likeCount','hidden')} | Comments: {s.get('commentCount','hidden')}")
    lines += ["", "Limitation: this first version uses YouTube Data API public/channel statistics. Audience retention, impressions, viewed-vs-swiped-away and viewer-online hours require YouTube Analytics API authorization (yt-analytics.readonly) and are not claimed here."]
    write_report("02_youtube_analytics.md", "YouTube Analytics Report", [("Channel performance", "\\n".join(lines))])

def generate_viral_content(videos):
    sections = []
    for v in videos[:15]:
        title = v.get("snippet", {}).get("title", "USA News Update")
        clean = re.sub(r"\\s+", " ", title).strip()
        sections.append(
            f"### Original: {clean}\\n"
            f"- Hook 1: Here is what you need to know about {clean}.\\n"
            f"- Hook 2: This latest update could matter to communities across the US.\\n"
            f"- Hook 3: A quick breakdown of the facts behind this developing story.\\n"
            f"- Title option 1: {clean} — What You Need to Know\\n"
            f"- Title option 2: Latest US Update: {clean}\\n"
            f"- Title option 3: The Key Facts About {clean}\\n"
            f"- বাংলা নোট: শিরোনামের বিষয় অনুযায়ী তিনটি Hook ও তিনটি Title-এর খসড়া; প্রকাশের আগে তথ্য যাচাই করতে হবে।\\n"
        )
    write_report("03_viral_hooks_titles.md", "Viral Hook & Title Generator", [
        ("Drafts", "\\n".join(sections) or "No recent videos found.")
    ])

def community_drafts(videos):
    topics = [v.get("snippet", {}).get("title", "") for v in videos[:5]]
    body = []
    for title in topics:
        body.append(f"### Topic: {title}\\nPoll question: Which part of this update would you like us to explain further?\\nOptions: Background | Local impact | What happens next | Key facts\\nবাংলা অর্থ: এই আপডেটের কোন দিকটি আমরা আরও ব্যাখ্যা করি?\\n")
    if not body:
        body = ["No recent video topics available for community drafts."]
    write_report("04_community_post_drafts.md", "Community Post Generator", [
        ("English post and Bengali meaning", "\\n".join(body)),
        ("নোট", "শুধু Draft তৈরি হয়। Community-তে স্বয়ংক্রিয়ভাবে প্রকাশ করা হয় না।")
    ])

def publishing_analyzer(videos):
    buckets = defaultdict(lambda: {"views": 0, "count": 0})
    for v in videos:
        sn = v.get("snippet", {})
        published = sn.get("publishedAt")
        if not published:
            continue
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            # Convert to US Eastern without requiring a separate timezone package.
            from zoneinfo import ZoneInfo
            local = dt.astimezone(ZoneInfo(ET_TZ))
            key = local.strftime("%A %H:00 ET")
            buckets[key]["views"] += int(v.get("statistics", {}).get("viewCount", 0))
            buckets[key]["count"] += 1
        except Exception:
            continue
    lines = []
    for key, val in sorted(buckets.items(), key=lambda x: (-(x[1]["views"] / max(1,x[1]["count"]))))[:15]:
        lines.append(f"- {key}: average views {val['views']//max(1,val['count'])} across {val['count']} video(s)")
    if not lines:
        lines = ["Not enough publication history to estimate a best time yet."]
    lines.append("This is a rough historical correlation, not proof that a time causes higher reach. YouTube Studio audience-online analytics can improve this after Analytics API authorization.")
    write_report("05_publishing_time_analysis.md", "Best Publishing Time Analyzer", [("Historical upload performance by hour", "\\n".join(lines))])

def weekly_report(videos):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = []
    for v in videos:
        try:
            dt = datetime.fromisoformat(v.get("snippet", {}).get("publishedAt","").replace("Z","+00:00"))
            if dt >= cutoff:
                recent.append(v)
        except Exception:
            pass
    views = sum(int(v.get("statistics", {}).get("viewCount", 0)) for v in recent)
    likes = sum(int(v.get("statistics", {}).get("likeCount", 0)) for v in recent)
    comments = sum(int(v.get("statistics", {}).get("commentCount", 0)) for v in recent)
    top = sorted(recent, key=lambda v: int(v.get("statistics", {}).get("viewCount", 0)), reverse=True)[:5]
    top_lines = [f"- {v.get('snippet',{}).get('title','Untitled')} — {v.get('statistics',{}).get('viewCount','0')} views" for v in top]
    write_report("06_weekly_growth_report.md", "Weekly Growth Report", [
        ("Last 7 days", f"- Videos: {len(recent)}\\n- Current views on these videos: {views}\\n- Likes: {likes}\\n- Comments: {comments}"),
        ("Top videos", "\\n".join(top_lines) or "No recent uploads in the last 7 days."),
        ("বাংলা পরামর্শ", "যে বিষয় ও ভিডিওতে দর্শক বেশি সাড়া দিয়েছে, পরের সপ্তাহে সেই ধরনের তথ্যভিত্তিক কনটেন্ট পরীক্ষা করুন।")
    ])

def main():
    selected = [x for x in ["SMART_ENGAGEMENT","YOUTUBE_ANALYTICS","VIRAL_CONTENT","COMMUNITY_POSTS","PUBLISHING_ANALYZER","WEEKLY_REPORT"] if enabled(x)]
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
