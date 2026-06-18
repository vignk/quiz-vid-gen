## app.py

```python
import io
import json
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

import gspread
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

app = FastAPI(title="Quiz Video Generator")

BASE_DIR = Path(__file__).parent
TMP_DIR = BASE_DIR / "tmp"
OUT_DIR = BASE_DIR / "rendered"
CREDS_DIR = BASE_DIR / "credentials"

for p in (TMP_DIR, OUT_DIR, CREDS_DIR):
    p.mkdir(exist_ok=True)

SHEETS_DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class RunRequest(BaseModel):
    sheet_id: str
    worksheet_name: str = "Sheet1"
    drive_folder_id: str
    youtube_title: str
    youtube_description: str = ""
    youtube_tags: List[str] = Field(default_factory=list)
    privacy_status: str = "private"
    category_id: str = "27"
    seconds_per_question: int = 7
    answer_reveal_after: int = 4
    intro_duration: int = 2
    outro_duration: int = 2
    width: int = 1080
    height: int = 1920
    intro_text: str = "Quiz Time"
    outro_text: str = "Subscribe for more quizzes"
    channel_watermark: str = ""
    music_volume: float = 0.12


def _write_json_secret(env_key: str, filename: str) -> Path:
    raw = os.environ.get(env_key)
    if not raw:
        raise RuntimeError(f"Missing environment variable: {env_key}")
    path = CREDS_DIR / filename
    path.write_text(raw, encoding="utf-8")
    return path


def get_service_account_credentials():
    sa_path = _write_json_secret("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
    return service_account.Credentials.from_service_account_file(
        sa_path,
        scopes=SHEETS_DRIVE_SCOPES,
    )


def get_sheet_rows(sheet_id: str, worksheet_name: str):
    creds = get_service_account_credentials()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(worksheet_name)
    return ws.get_all_records()


def get_drive_service():
    creds = get_service_account_credentials()
    return build("drive", "v3", credentials=creds)


def list_audio_files(folder_id: str):
    drive = get_drive_service()
    resp = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false and mimeType contains 'audio/'",
        fields="files(id,name,mimeType)",
        pageSize=100,
    ).execute()
    return resp.get("files", [])


def download_random_music(folder_id: str, work_dir: Path) -> Path:
    files = list_audio_files(folder_id)
    if not files:
        raise RuntimeError("No audio files found in the supplied Google Drive folder")

    chosen = random.choice(files)
    ext = Path(chosen["name"]).suffix or ".mp3"
    output_path = work_dir / f"music{ext}"

    request = get_drive_service().files().get_media(fileId=chosen["id"])
    with io.FileIO(output_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    return output_path


def get_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size=size)
    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    lines = []
    current = ""
    for word in text.split():
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def make_centered_card(draw, x1, y1, x2, y2, fill, outline):
    draw.rounded_rectangle((x1, y1, x2, y2), radius=36, fill=fill, outline=outline, width=4)


def draw_centered_text(draw, text, font, box, fill, line_gap=14):
    x1, y1, x2, y2 = box
    lines = wrap_text(draw, text, font, x2 - x1 - 40)
    total_h = 0
    dims = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        dims.append((line, w, h))
        total_h += h + line_gap
    total_h -= line_gap if dims else 0
    y = y1 + ((y2 - y1) - total_h) // 2
    for line, w, h in dims:
        x = x1 + ((x2 - x1) - w) // 2
        draw.text((x, y), line, font=font, fill=fill)
        y += h + line_gap


def make_intro_screen(text: str, width: int, height: int, work_dir: Path) -> Path:
    img = Image.new("RGB", (width, height), (10, 77, 89))
    draw = ImageDraw.Draw(img)
    title_font = get_font(82, bold=True)
    sub_font = get_font(40, bold=False)

    make_centered_card(draw, 70, 260, width - 70, height - 260, (252, 248, 240), (255, 210, 120))
    draw_centered_text(draw, text, title_font, (120, 420, width - 120, 930), (20, 20, 20))
    draw_centered_text(draw, "Get ready to answer before time runs out", sub_font, (120, 980, width - 120, 1180), (70, 70, 70))

    path = work_dir / "intro.png"
    img.save(path, quality=95)
    return path


def make_outro_screen(text: str, width: int, height: int, work_dir: Path) -> Path:
    img = Image.new("RGB", (width, height), (247, 242, 229))
    draw = ImageDraw.Draw(img)
    title_font = get_font(72, bold=True)
    sub_font = get_font(42, bold=False)

    make_centered_card(draw, 70, 260, width - 70, height - 260, (255, 255, 255), (13, 92, 102))
    draw_centered_text(draw, text, title_font, (110, 460, width - 110, 920), (13, 92, 102))
    draw_centered_text(draw, "Like • Share • Subscribe", sub_font, (110, 980, width - 110, 1140), (50, 50, 50))

    path = work_dir / "outro.png"
    img.save(path, quality=95)
    return path


def make_thumbnail_image(title: str, work_dir: Path) -> Path:
    thumb_w, thumb_h = 1280, 720
    img = Image.new("RGB", (thumb_w, thumb_h), (10, 77, 89))
    draw = ImageDraw.Draw(img)

    title_font = get_font(78, bold=True)
    small_font = get_font(34, bold=False)

    draw.rounded_rectangle((40, 40, thumb_w - 40, thumb_h - 40), radius=32, fill=(248, 245, 237), outline=(255, 191, 73), width=5)
    draw_centered_text(draw, title, title_font, (90, 120, thumb_w - 90, 420), (22, 22, 22), line_gap=18)
    draw_centered_text(draw, "Test your knowledge", small_font, (90, 470, thumb_w - 90, 560), (13, 92, 102))
    draw.ellipse((930, 180, 1120, 370), fill=(255, 191, 73))
    draw.text((980, 230), "?", font=get_font(110, bold=True), fill=(10, 77, 89))

    path = work_dir / "thumbnail.png"
    img.save(path, quality=95)
    return path


def make_question_slides(idx: int, row: dict, width: int, height: int, intro_text: str, watermark: str, work_dir: Path):
    question = str(row.get("question", "")).strip()
    if not question:
        raise RuntimeError(f"Missing question text on row {idx}")

    options = [
        str(row.get("option_a", "")).strip(),
        str(row.get("option_b", "")).strip(),
        str(row.get("option_c", "")).strip(),
        str(row.get("option_d", "")).strip(),
    ]
    options = [o for o in options if o]
    answer = str(row.get("answer", "")).strip()

    def draw_common(include_answer: bool):
        img = Image.new("RGB", (width, height), (247, 242, 229))
        draw = ImageDraw.Draw(img)

        title_font = get_font(42, bold=True)
        q_font = get_font(58, bold=True)
        opt_font = get_font(42, bold=False)
        ans_font = get_font(44, bold=True)
        wm_font = get_font(28, bold=False)

        draw.rounded_rectangle((36, 36, width - 36, height - 36), radius=42, fill=(255, 252, 246), outline=(13, 92, 102), width=5)
        draw.text((78, 72), intro_text, fill=(13, 92, 102), font=title_font)
        draw.text((width - 220, 78), f"Q{idx}", fill=(90, 90, 90), font=title_font)

        y = 180
        for line in wrap_text(draw, question, q_font, width - 160):
            draw.text((80, y), line, fill=(24, 24, 24), font=q_font)
            y += 74

        y += 36
        label_colors = [(212, 88, 42), (78, 119, 33), (115, 67, 152), (188, 124, 8)]
        for i, option in enumerate(options):
            bx1, by1, bx2, by2 = 80, y - 6, width - 80, y + 92
            draw.rounded_rectangle((bx1, by1, bx2, by2), radius=28, fill=(245, 247, 250), outline=label_colors[i % len(label_colors)], width=3)
            draw.ellipse((102, y + 16, 150, y + 64), fill=label_colors[i % len(label_colors)])
            draw.text((118, y + 18), chr(65 + i), fill=(255, 255, 255), font=get_font(28, bold=True))
            opt_lines = wrap_text(draw, option, opt_font, width - 240)
            oy = y + 14
            for line in opt_lines[:2]:
                draw.text((176, oy), line, fill=(50, 50, 50), font=opt_font)
                oy += 42
            y += 122

        if include_answer and answer:
            ay = height - 250
            draw.rounded_rectangle((80, ay, width - 80, ay + 140), radius=32, fill=(13, 92, 102))
            draw.text((110, ay + 42), f"Answer: {answer}", fill=(255, 255, 255), font=ans_font)

        if watermark:
            bbox = draw.textbbox((0, 0), watermark, font=wm_font)
            tw = bbox[2] - bbox[0]
            draw.text((width - tw - 60, height - 70), watermark, fill=(120, 120, 120), font=wm_font)

        return img

    base_path = work_dir / f"question_base_{idx:03d}.png"
    answer_path = work_dir / f"question_answer_{idx:03d}.png"
    draw_common(False).save(base_path, quality=95)
    draw_common(True).save(answer_path, quality=95)
    return base_path, answer_path


def run_ffmpeg(cmd: list):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return result


def make_image_clip(image_path: Path, duration: int, out_path: Path):
    run_ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-t", str(duration),
        "-vf", "fps=30,format=yuv420p",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def make_question_clip(base_png: Path, answer_png: Path, duration: int, reveal_after: int, out_path: Path):
    fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    countdown_expr = f"%{{eif\\:{duration}-t\\:d}}"

    filter_complex = (
        f"[0:v]scale=1080:1920,zoompan=z='min(zoom+0.0008,1.08)':d=1:s=1080x1920:fps=30[bg];"
        f"[1:v]scale=1080:1920[ans];"
        f"[bg][ans]overlay=0:0:enable='gte(t,{reveal_after})',"
        f"drawtext=fontfile={fontfile}:text='{countdown_expr}':"
        f"fontcolor=white:fontsize=72:box=1:boxcolor=black@0.45:boxborderw=18:"
        f"x=(w-text_w)/2:y=140"
    )

    run_ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(base_png),
        "-loop", "1", "-i", str(answer_png),
        "-t", str(duration),
        "-filter_complex", filter_complex,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def concat_videos(video_paths: List[Path], out_path: Path):
    list_file = out_path.parent / "video_list.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for vp in video_paths:
            f.write(f"file '{vp.as_posix()}'\n")

    run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path)
    ])


def add_music(video_path: Path, music_path: Path, music_volume: float, out_path: Path):
    run_ffmpeg([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1", "-i", str(music_path),
        "-filter_complex", f"[1:a]volume={music_volume}[bgm]",
        "-map", "0:v:0",
        "-map", "[bgm]",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        str(out_path)
    ])


def get_youtube_credentials():
    token_raw = os.environ.get("YOUTUBE_TOKEN_JSON")
    if not token_raw:
        raise RuntimeError("Missing YOUTUBE_TOKEN_JSON. Generate it locally and save it in Render.")
    creds = Credentials.from_authorized_user_info(json.loads(token_raw), YOUTUBE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def upload_to_youtube(video_path: Path, title: str, description: str, tags: List[str], privacy_status: str, category_id: str):
    creds = get_youtube_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        _, response = request.next_chunk()

    return response


def upload_thumbnail(video_id: str, thumbnail_path: Path):
    creds = get_youtube_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    media = MediaFileUpload(str(thumbnail_path), mimetype="image/png")
    return youtube.thumbnails().set(videoId=video_id, media_body=media).execute()


@app.get("/")
def root():
    return {
        "app": "quiz-video-render-app",
        "health": "/health",
        "run": "POST /run"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/music-files/{drive_folder_id}")
def music_files(drive_folder_id: str):
    try:
        files = list_audio_files(drive_folder_id)
        return {"count": len(files), "files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run")
def run_pipeline(payload: RunRequest):
    work_dir = Path(tempfile.mkdtemp(prefix="quiz_job_", dir=TMP_DIR))
    try:
        rows = get_sheet_rows(payload.sheet_id, payload.worksheet_name)
        if not rows:
            raise RuntimeError("No rows found in the worksheet")

        intro_png = make_intro_screen(payload.intro_text, payload.width, payload.height, work_dir)
        outro_png = make_outro_screen(payload.outro_text, payload.width, payload.height, work_dir)
        thumb_png = make_thumbnail_image(payload.youtube_title, work_dir)

        intro_clip = work_dir / "intro.mp4"
        outro_clip = work_dir / "outro.mp4"
        make_image_clip(intro_png, payload.intro_duration, intro_clip)
        make_image_clip(outro_png, payload.outro_duration, outro_clip)

        question_clips = []
        for idx, row in enumerate(rows, start=1):
            if not str(row.get("question", "")).strip():
                continue

            base_png, answer_png = make_question_slides(
                idx=idx,
                row=row,
                width=payload.width,
                height=payload.height,
                intro_text=payload.intro_text,
                watermark=payload.channel_watermark,
                work_dir=work_dir
            )

            clip_path = work_dir / f"qclip_{idx:03d}.mp4"
            make_question_clip(
                base_png=base_png,
                answer_png=answer_png,
                duration=payload.seconds_per_question,
                reveal_after=payload.answer_reveal_after,
                out_path=clip_path
            )
            question_clips.append(clip_path)

        if not question_clips:
            raise RuntimeError("No usable question rows found")

        merged_silent = work_dir / "merged_silent.mp4"
        safe_name = "".join(c if c.isalnum() else "_" for c in payload.youtube_title)[:60] or "quiz_video"
        final_video = OUT_DIR / f"{safe_name}.mp4"

        concat_videos([intro_clip] + question_clips + [outro_clip], merged_silent)

        music_path = download_random_music(payload.drive_folder_id, work_dir)
        add_music(merged_silent, music_path, payload.music_volume, final_video)

        upload_response = upload_to_youtube(
            video_path=final_video,
            title=payload.youtube_title,
            description=payload.youtube_description,
            tags=payload.youtube_tags,
            privacy_status=payload.privacy_status,
            category_id=payload.category_id
        )

        thumbnail_response = upload_thumbnail(upload_response["id"], thumb_png)

        return {
            "success": True,
            "rows_read": len(rows),
            "question_clips": len(question_clips),
            "video_file": str(final_video),
            "youtube_video_id": upload_response.get("id"),
            "thumbnail_uploaded": True,
            "thumbnail_response": thumbnail_response
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
```
