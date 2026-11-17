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
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
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
    title = (headline or "USA News Update").strip()
    if len(title) > 95:
        title = title[:92].rsplit(" ", 1)[0] + "..."
    description = (
        "Digital Orbit | USA News Briefing\n\n"
        "A concise news update based on the linked source.\n"
        + (f"Source: {source_url}\n\n" if source_url else "")
        + "Subscribe for more news briefings.\n"
        "#USANews #DigitalOrbit #NewsShorts"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description[:4900],
            "categoryId": "25",
            "tags": ["USA News", "Digital Orbit", "News Briefing", "News Shorts"]
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
