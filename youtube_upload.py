import os
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
VIDEO_DIR = Path("output_videos")
EASTERN = ZoneInfo("America/New_York")
PUBLISH_SLOTS = [(9, 0), (13, 0), (17, 0)]


def get_youtube_service():
    token_json = os.environ.get("YOUTUBE_TOKEN_JSON", "").strip()
    if not token_json:
        raise RuntimeError("YOUTUBE_TOKEN_JSON GitHub Secret is missing.")
    credentials = Credentials.from_authorized_user_info(
        json.loads(token_json), scopes=SCOPES
    )
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid:
        raise RuntimeError("YouTube credentials invalid; check YOUTUBE_TOKEN_JSON.")
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def next_publish_time(index, now=None):
    now = now or datetime.now(EASTERN)
    candidates = []
    for day_offset in range(4):
        day = (now + timedelta(days=day_offset)).date()
        for hour, minute in PUBLISH_SLOTS:
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=EASTERN)
            if slot > now + timedelta(minutes=15):
                candidates.append(slot)
    candidates.sort()
    if not candidates:
        raise RuntimeError("Could not find a future YouTube publishing slot.")
    return candidates[min(index, len(candidates) - 1)].isoformat(timespec="seconds")


def upload_video(youtube, video_path, publish_at):
    title = video_path.stem.replace("_", " ").title()
    description = (
        "Digital Orbit US — News Briefing\n\n"
        "Automated news summary. Refer to original news sources for details.\n"
        "Visuals are illustrative where applicable.\n\n"
        "#USNews #DigitalOrbitUS #Shorts"
    )
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": ["US news", "news briefing", "Digital Orbit US", "Shorts"],
            "categoryId": "25",
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media, notifySubscribers=False
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploading {video_path.name}: {int(status.progress() * 100)}%")
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"No video ID returned for {video_path.name}")
    print(f"UPLOAD SUCCESS: {video_path.name}")
    print(f"SCHEDULED EASTERN TIME: {publish_at}")
    print(f"VIDEO URL: https://www.youtube.com/watch?v={video_id}")


def main():
    videos = sorted(VIDEO_DIR.glob("news_*.mp4"))[:3]
    if not videos:
        print("No news_*.mp4 videos found in output_videos/.")
        sys.exit(1)
    print(f"Found {len(videos)} video(s) to schedule.")
    youtube = get_youtube_service()
    failures = 0
    for index, video in enumerate(videos):
        try:
            upload_video(youtube, video, next_publish_time(index))
        except Exception as exc:
            failures += 1
            print(f"UPLOAD FAILED: {video.name}: {str(exc)[:500]}")
    if failures:
        raise RuntimeError(f"{failures} upload(s) failed.")
    print("All available videos were scheduled.")


if __name__ == "__main__":
    main()
