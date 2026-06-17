## local_oauth.py

Use this once on your local machine to get the token JSON for YouTube uploads, because YouTube video upload requires OAuth user authorization. [4]

```python
import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

BASE_DIR = Path(__file__).parent
CREDS_DIR = BASE_DIR / "credentials"
CREDS_DIR.mkdir(exist_ok=True)

raw = os.environ.get("YOUTUBE_CLIENT_SECRETS_JSON")
if not raw:
    raise SystemExit("Set YOUTUBE_CLIENT_SECRETS_JSON with your OAuth client JSON first.")

client_path = CREDS_DIR / "client_secrets.json"
client_path.write_text(raw, encoding="utf-8")

flow = InstalledAppFlow.from_client_secrets_file(str(client_path), YOUTUBE_SCOPES)
creds = flow.run_local_server(port=0)

print("Copy this output into Render as YOUTUBE_TOKEN_JSON:\n")
print(creds.to_json())
`
