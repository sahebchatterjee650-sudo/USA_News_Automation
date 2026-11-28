"""Private-only uploader for long-form Digital Orbit videos."""
import json,os,time
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
SCOPES=["https://www.googleapis.com/auth/youtube.upload"]
_service=None
def get_service():
    global _service
    if _service is not None: return _service
    raw=os.getenv("YOUTUBE_TOKEN_JSON","").strip()
    if not raw: raise RuntimeError("Missing existing YOUTUBE_TOKEN_JSON secret.")
    creds=Credentials.from_authorized_user_info(json.loads(raw),SCOPES)
    if creds.expired and creds.refresh_token: creds.refresh(Request())
    if not creds.valid: raise RuntimeError("Existing YouTube OAuth token invalid/revoked.")
    _service=build("youtube","v3",credentials=creds,cache_discovery=False)
    return _service
def upload_long_video_private(video_path,headline,source_url=""):
    path=Path(video_path)
    if not path.exists() or path.stat().st_size<500000: raise RuntimeError("Long-form MP4 missing/undersized.")
    title=" ".join((headline or "Digital Orbit News Explainer").split())[:95]
    desc="A source-based Digital Orbit news explainer.\n\n"+(f"Publisher source: {source_url}\n\n" if source_url else "")+"Clear facts. Important stories.\n#USNews #NewsExplainer #DigitalOrbit"
    body={"snippet":{"title":title,"description":desc[:4900],"categoryId":"25","tags":["Digital Orbit","US News","News Explainer"]},
          "status":{"privacyStatus":"private","selfDeclaredMadeForKids":False}}
    youtube=get_service(); last=None
    for attempt in range(1,4):
        try:
            media=MediaFileUpload(str(path),mimetype="video/mp4",resumable=True,chunksize=8*1024*1024)
            req=youtube.videos().insert(part="snippet,status",body=body,media_body=media,notifySubscribers=False)
            response=None
            while response is None: _,response=req.next_chunk()
            vid=response["id"]; result={"video_id":vid,"url":f"https://www.youtube.com/watch?v={vid}","privacy_status":"private"}
            print("LONG VIDEO PRIVATE UPLOAD:",json.dumps(result)); return result
        except Exception as exc:
            last=exc; print(f"Private long upload attempt {attempt}/3 failed: {str(exc)[:350]}")
            if attempt<3: time.sleep(attempt*10)
    raise RuntimeError(f"Long video upload failed after 3 attempts: {last}")
