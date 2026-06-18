##```python
import csv
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont

app = FastAPI(title="Quiz Video Generator")

BASE_DIR = Path(__file__).parent
TMP_DIR = BASE_DIR / "tmp"
OUT_DIR = BASE_DIR / "output"

TMP_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

app.mount("/files", StaticFiles(directory=str(OUT_DIR)), name="files")


class QuizRow(BaseModel):
    question: str
    option_a: str = ""
    option_b: str = ""
    option_c: str = ""
    option_d: str = ""
    answer: str = ""


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
    words = text.split()
    lines = []
    current = ""
    for word in words:
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


def draw_centered_text(draw, text, font, box, fill, line_gap=12):
    x1, y1, x2, y2 = box
    lines = wrap_text(draw, text, font, x2 - x1 - 30)
    dims = []
    total_h = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        dims.append((line, w, h))
        total_h += h + line_gap
    if dims:
        total_h -= line_gap
    y = y1 + ((y2 - y1) - total_h) // 2
    for line, w, h in dims:
        x = x1 + ((x2 - x1) - w) // 2
        draw.text((x, y), line, font=font, fill=fill)
        y += h + line_gap


def make_intro_screen(text: str, width: int, height: int, work_dir: Path) -> Path:
    img = Image.new("RGB", (width, height), (18, 88, 104))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((60, 220, width - 60, height - 220), radius=40, fill=(249, 245, 235), outline=(255, 196, 85), width=5)
    draw_centered_text(draw, text, get_font(82, True), (100, 420, width - 100, 950), (25, 25, 25))
    draw_centered_text(draw, "Answer before the timer ends", get_font(40, False), (100, 980, width - 100, 1140), (80, 80, 80))
    path = work_dir / "intro.png"
    img.save(path, quality=95)
    return path


def make_outro_screen(text: str, width: int, height: int, work_dir: Path) -> Path:
    img = Image.new("RGB", (width, height), (247, 242, 230))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((60, 220, width - 60, height - 220), radius=40, fill=(255, 255, 255), outline=(18, 88, 104), width=5)
    draw_centered_text(draw, text, get_font(72, True), (100, 450, width - 100, 930), (18, 88, 104))
    draw_centered_text(draw, "Download and upload to YouTube", get_font(38, False), (100, 980, width - 100, 1120), (70, 70, 70))
    path = work_dir / "outro.png"
    img.save(path, quality=95)
    return path


def make_question_images(idx: int, row: QuizRow, width: int, height: int, watermark: str, work_dir: Path):
    def render(include_answer: bool):
        img = Image.new("RGB", (width, height), (247, 242, 230))
        draw = ImageDraw.Draw(img)

        title_font = get_font(42, True)
        q_font = get_font(56, True)
        opt_font = get_font(40, False)
        ans_font = get_font(44, True)
        small_font = get_font(28, False)

        draw.rounded_rectangle((36, 36, width - 36, height - 36), radius=36, fill=(255, 252, 246), outline=(18, 88, 104), width=4)
        draw.text((74, 70), f"Question {idx}", fill=(18, 88, 104), font=title_font)

        y = 180
        for line in wrap_text(draw, row.question, q_font, width - 160):
            draw.text((80, y), line, fill=(25, 25, 25), font=q_font)
            y += 72

        y += 25
        options = [row.option_a, row.option_b, row.option_c, row.option_d]
        colors = [(210, 80, 60), (60, 140, 80), (110, 70, 170), (210, 145, 20)]
        for i, opt in enumerate([o for o in options if o.strip()]):
            draw.rounded_rectangle((80, y, width - 80, y + 92), radius=24, fill=(243, 246, 249), outline=colors[i % 4], width=3)
            draw.ellipse((100, y + 18, 146, y + 64), fill=colors[i % 4])
            draw.text((116, y + 18), chr(65 + i), fill=(255, 255, 255), font=get_font(26, True))
            lines = wrap_text(draw, opt, opt_font, width - 250)
            oy = y + 14
            for line in lines[:2]:
                draw.text((170, oy), line, fill=(55, 55, 55), font=opt_font)
                oy += 40
            y += 118

        if include_answer and row.answer.strip():
            ay = height - 235
            draw.rounded_rectangle((80, ay, width - 80, ay + 125), radius=28, fill=(18, 88, 104))
            draw.text((112, ay + 38), f"Answer: {row.answer}", fill=(255, 255, 255), font=ans_font)

        if watermark.strip():
            bbox = draw.textbbox((0, 0), watermark, font=small_font)
            tw = bbox[2] - bbox[0]
            draw.text((width - tw - 60, height - 65), watermark, fill=(125, 125, 125), font=small_font)

        return img

    base_path = work_dir / f"q_{idx:03d}_base.png"
    answer_path = work_dir / f"q_{idx:03d}_answer.png"
    render(False).save(base_path, quality=95)
    render(True).save(answer_path, quality=95)
    return base_path, answer_path


def run_ffmpeg(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def image_to_clip(image_path: Path, duration: int, out_path: Path):
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


def question_to_clip(base_png: Path, answer_png: Path, duration: int, reveal_after: int, width: int, height: int, out_path: Path):
    fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    countdown_expr = f"%{{eif\\:{duration}-t\\:d}}"

    filter_complex = (
        f"[0:v]scale={width}:{height},zoompan=z='min(zoom+0.0008,1.08)':d=1:s={width}x{height}:fps=30[bg];"
        f"[1:v]scale={width}:{height}[ans];"
        f"[bg][ans]overlay=0:0:enable='gte(t,{reveal_after})',"
        f"drawtext=fontfile={fontfile}:text='{countdown_expr}':"
        f"fontcolor=white:fontsize=70:box=1:boxcolor=black@0.45:boxborderw=16:"
        f"x=(w-text_w)/2:y=130"
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


def concat_clips(clips: List[Path], out_path: Path):
    list_file = out_path.parent / "concat.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for clip in clips:
            f.write(f"file '{clip.as_posix()}'\n")

    run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path)
    ])


def add_music(video_path: Path, music_path: Optional[Path], volume: float, out_path: Path):
    if music_path and music_path.exists():
        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex", f"[1:a]volume={volume}[bgm]",
            "-map", "0:v:0",
            "-map", "[bgm]",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            str(out_path)
        ])
    else:
        shutil.copy(video_path, out_path)


def parse_csv_text(csv_text: str) -> List[QuizRow]:
    rows = []
    reader = csv.DictReader(csv_text.splitlines())
    for row in reader:
        rows.append(QuizRow(
            question=row.get("question", "") or "",
            option_a=row.get("option_a", "") or "",
            option_b=row.get("option_b", "") or "",
            option_c=row.get("option_c", "") or "",
            option_d=row.get("option_d", "") or "",
            answer=row.get("answer", "") or ""
        ))
    return rows


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width,initial-scale=1"/>
      <title>Quiz Video Generator</title>
      <style>
        body{font-family:Arial,sans-serif;max-width:900px;margin:30px auto;padding:20px;background:#f7f2e9}
        textarea,input{width:100%;padding:12px;margin:8px 0 16px}
        button{padding:14px 20px;background:#125868;color:#fff;border:none;border-radius:8px;cursor:pointer}
        .card{background:#fff;padding:24px;border-radius:14px;box-shadow:0 4px 16px rgba(0,0,0,.08)}
        code{background:#f2f2f2;padding:2px 6px}
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Quiz Video Generator</h1>
        <p>Paste CSV content with headers: <code>question,option_a,option_b,option_c,option_d,answer</code></p>
        <form action="/generate" method="post" enctype="multipart/form-data">
          <label>Video title</label>
          <input type="text" name="video_title" required value="My Quiz Video"/>

          <label>Intro text</label>
          <input type="text" name="intro_text" value="Quiz Time"/>

          <label>Outro text</label>
          <input type="text" name="outro_text" value="Thanks for watching"/>

          <label>Watermark</label>
          <input type="text" name="watermark" value="Your Channel"/>

          <label>CSV quiz data</label>
          <textarea name="csv_text" rows="12" required>question,option_a,option_b,option_c,option_d,answer
What is the capital of France?,Paris,London,Rome,Berlin,Paris
2 + 2 = ?,3,4,5,6,4</textarea>

          <label>Optional background music file</label>
          <input type="file" name="music_file" accept=".mp3,.wav,.m4a,.aac"/>

          <button type="submit">Generate Video</button>
        </form>
      </div>
    </body>
    </html>
    """


@app.post("/generate")
async def generate_video(
    request: Request,
    video_title: str = Form(...),
    intro_text: str = Form("Quiz Time"),
    outro_text: str = Form("Thanks for watching"),
    watermark: str = Form(""),
    csv_text: str = Form(...),
    music_file: UploadFile | None = File(default=None),
):
    work_dir = Path(tempfile.mkdtemp(prefix="job_", dir=TMP_DIR))
    try:
        quiz_rows = [r for r in parse_csv_text(csv_text) if r.question.strip()]
        if not quiz_rows:
            raise HTTPException(status_code=400, detail="No valid quiz rows found")

        width, height = 1080, 1920
        intro_png = make_intro_screen(intro_text, width, height, work_dir)
        outro_png = make_outro_screen(outro_text, width, height, work_dir)

        intro_clip = work_dir / "intro.mp4"
        outro_clip = work_dir / "outro.mp4"
        image_to_clip(intro_png, 2, intro_clip)
        image_to_clip(outro_png, 2, outro_clip)

        music_path = None
        if music_file and music_file.filename:
            ext = Path(music_file.filename).suffix or ".mp3"
            music_path = work_dir / f"music{ext}"
            with open(music_path, "wb") as f:
                f.write(await music_file.read())

        clips = [intro_clip]
        for idx, row in enumerate(quiz_rows, start=1):
            base_png, answer_png = make_question_images(idx, row, width, height, watermark, work_dir)
            clip_path = work_dir / f"clip_{idx:03d}.mp4"
            question_to_clip(base_png, answer_png, 7, 4, width, height, clip_path)
            clips.append(clip_path)

        clips.append(outro_clip)

        merged = work_dir / "merged.mp4"
        concat_clips(clips, merged)

        video_id = uuid.uuid4().hex[:12]
        safe_title = "".join(c if c.isalnum() else "_" for c in video_title)[:50] or "quiz_video"
        final_path = OUT_DIR / f"{safe_title}_{video_id}.mp4"

        add_music(merged, music_path, 0.12, final_path)

        base_url = str(request.base_url).rstrip("/")
        return JSONResponse({
            "success": True,
            "video_id": video_id,
            "filename": final_path.name,
            "download_url": f"{base_url}/download/{final_path.name}",
            "direct_url": f"{base_url}/files/{final_path.name}"
        })

    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="FFmpeg failed while generating the video")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.get("/download/{filename}")
def download_file(filename: str):
    file_path = OUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=filename
    )
##``
