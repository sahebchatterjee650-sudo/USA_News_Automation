import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ET = ZoneInfo("America/New_York")
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
_service = None
_first_publish_time = None


def get_youtube_service():
    global _service
    if _service is not None:
        return _service

    raw = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if not raw:
        raise RuntimeError(
            "Missing GitHub Secret YOUTUBE_TOKEN_JSON. "
            "Create a YouTube OAuth token locally and add the complete token.json as a repository secret."
        )

    try:
        token_info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("YOUTUBE_TOKEN_JSON is not valid JSON.") from exc

    credentials = Credentials.from_authorized_user_info(token_info, SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid:
        raise RuntimeError(
            "YouTube OAuth token is invalid or revoked. Generate a fresh token.json and update YOUTUBE_TOKEN_JSON."
        )

    _service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    return _service



def normalize_title(value):
    """Normalize titles so punctuation/case differences do not create duplicates."""
    import re
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def get_recent_video_titles(max_pages=10):
    """Read recent channel uploads so a later scheduled run skips previously posted stories."""
    youtube = get_youtube_service()
    channel_response = youtube.channels().list(
        part="contentDetails",
        mine=True
    ).execute()
    items = channel_response.get("items", [])
    if not items:
        raise RuntimeError("Could not identify the authorized YouTube channel.")

    uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    titles = set()
    page_token = None

    for _ in range(max_pages):
        response = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist,
            maxResults=50,
            pageToken=page_token
        ).execute()
        for item in response.get("items", []):
            title = item.get("snippet", {}).get("title", "")
            normalized = normalize_title(title)
            if normalized:
                titles.add(normalized)
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    print("Loaded recent YouTube titles for duplicate check:", len(titles))
    return titles

def first_future_publish_time():
    now = datetime.now(ET)
    # Three daily US Eastern release windows. If today's slot has passed,
    # choose the next future slot; never send a past publishAt value.
    slots = [(9, 0), (13, 0), (17, 0)]
    for day_offset in range(0, 3):
        day = (now + timedelta(days=day_offset)).date()
        for hour, minute in slots:
            candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)
            if candidate >= now + timedelta(minutes=10):
                return candidate
    raise RuntimeError("Could not calculate a future YouTube publication time.")


def upload_video(video_path, headline, source_url="", index=0):
    global _first_publish_time
    path = Path(video_path)
    if not path.exists() or path.stat().st_size < 100_000:
        raise RuntimeError(f"Video file is missing or too small: {path}")

    if _first_publish_time is None:
        _first_publish_time = first_future_publish_time()
    publish_at = _first_publish_time + timedelta(minutes=15 * index)

    youtube = get_youtube_service()
    # Keep the publisher's factual headline recognizable; avoid misleading clickbait.
    # Keep the core news entity/action visible on mobile; avoid misleading clickbait.
    title = " ".join((headline or "USA News Update").split()).strip()
    if len(title) > 78:
        title = title[:75].rsplit(" ", 1)[0].rstrip(" -:|,") + "..."

    import re
    words = re.findall(r"[A-Za-z0-9]{3,}", title)
    stop = {"the", "and", "for", "with", "from", "that", "this", "after", "over", "says", "what", "when", "how", "are", "was", "were", "has", "have"}
    topic_tags = []
    for word in words:
        candidate = word.lower()
        if candidate not in stop and candidate not in [x.lower() for x in topic_tags]:
            topic_tags.append(candidate)
        if len(topic_tags) >= 8:
            break

    description = (
        f"{title}\\n\\n"
        "The key facts and latest known development in this story, in a short, source-based briefing. "
        "What matters most to you about this update? Share your view below.\\n\\n"
        + (f"Original reporting / source: {source_url}\\n\\n" if source_url else "")
        + "Follow Digital Orbit for concise US news updates.\\n"
        "#Shorts #USNews #News"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description[:4900],
            "categoryId": "25",
            "tags": list(dict.fromkeys(["Digital Orbit", "US News", "News Shorts", "News Briefing"] + topic_tags))[:15]
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
            "selfDeclaredMadeForKids": False
        }
    }

    last_error = None
    for attempt in range(1, 4):
        try:
            media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
                notifySubscribers=False
            )
            response = None
            while response is None:
                _, response = request.next_chunk()
            video_id = response["id"]
            result = {
                "video_id": video_id,
                "url": f"https://www.youtube.com/shorts/{video_id}",
                "publish_at_eastern": publish_at.isoformat()
            }
            print("YOUTUBE SCHEDULED:", json.dumps(result))
            return result
        except Exception as exc:
            last_error = exc
            print(f"YouTube upload attempt {attempt}/3 failed: {str(exc)[:500]}")
            if attempt < 3:
                time.sleep(attempt * 10)

    raise RuntimeError(f"YouTube upload failed after 3 attempts: {last_error}")
