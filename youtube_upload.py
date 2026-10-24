

import os
import json
import glob
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
VIDEO_DIR = Path("output_videos")


def get_youtube_service():
    token_json = os.environ.get("YOUTUBE_TOKEN_JSON", "").strip()

    if not token_json:
        raise RuntimeError(
            "YOUTUBE_TOKEN_JSON GitHub Secret is missing."
        )

    token_info = json.loads(token_json)

    credentials = Credentials.from_authorized_user_info(
        token_info,
        scopes=SCOPES,
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials.valid:
        raise RuntimeError(
            "YouTube credentials are invalid. "
            "Create a fresh OAuth token with youtube.upload scope."
        )

    return build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


def upload_video(youtube, video_path):
    title = video_path.stem.replace("_", " ").title()
    description = (
        "Digital Orbit US — News Briefing\n\n"
        "This video is an automated news summary. "
        "Please refer to the original news sources for details.\n\n"
        "#USNews #DigitalOrbitUS #Shorts"
    )

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": [
                "US news",
                "news briefing",
                "Digital Orbit US",
                "Shorts",
            ],
            "categoryId": "25",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=False,
    )

    response = None

    while response is None:
        status, response = request.next_chunk()
        if status:
            print(
                f"Uploading {video_path.name}: "
                f"{int(status.progress() * 100)}%"
            )

    video_id = response.get("id")

    if not video_id:
        raise RuntimeError(
            f"YouTube did not return a video ID for {video_path.name}"
        )

    print(f"UPLOAD SUCCESS: {video_path.name}")
    print(f"VIDEO ID: {video_id}")
    print(f"PRIVATE URL: https://www.youtube.com/watch?v={video_id}")


def main():
    videos = sorted(VIDEO_DIR.glob("*.mp4"))

    if not videos:
        print("No MP4 videos found in output_videos/.")
        sys.exit(1)

    print(f"Found {len(videos)} video(s) to upload.")

    youtube = get_youtube_service()
    failures = 0

    for video in videos:
        try:
            upload_video(youtube, video)
        except Exception as exc:
            failures += 1
            print(
                f"UPLOAD FAILED: {video.name}: "
                f"{str(exc)[:500]}"
            )

    if failures:
        raise RuntimeError(
            f"{failures} video(s) failed to upload."
        )

    print("All videos uploaded privately.")


if __name__ == "__main__":
    main()
