import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

client_file = Path("client_secret.json")
if not client_file.exists():
    raise SystemExit(
        "Put the OAuth Desktop client JSON downloaded from Google Cloud in this folder "
        "and rename it client_secret.json."
    )

flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
credentials = flow.run_local_server(
    host="localhost",
    port=0,
    authorization_prompt_message="Open this URL in your browser: {url}",
    success_message="YouTube authorization complete. You can close this browser tab.",
    access_type="offline",
    prompt="consent"
)

Path("token.json").write_text(credentials.to_json(), encoding="utf-8")
print("Created token.json. Add its complete contents to GitHub Actions secret YOUTUBE_TOKEN_JSON.")
