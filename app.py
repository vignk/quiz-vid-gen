import csv
import html
import shutil
import subprocess
import tempfile
import uuid
import logging
import sys
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("quiz-video")

def normalize_key(key: str) -> str:
    return (key or "").strip().lower().replace(" ", "_")


def first_value(row: dict, keys: List[str]) -> str:
    for key in keys:
        value = row.get(key, "")
        if str(value).strip():
            return str(value).strip()
    return ""


def parse_csv_text(csv_text: str) -> List[QuizRow]:
    cleaned_lines = [line for line in csv_text.splitlines() if line.strip()]
    if not cleaned_lines:
        return []

    reader = csv.DictReader(cleaned_lines)
    parsed_rows = []

    for raw_row in reader:
        row = {normalize_key(k): (v or "").strip() for k, v in raw_row.items() if k}

        item = QuizRow(
            question=first_value(row, ["question", "questions", "ques"]),
            option_a=first_value(row, ["option_a", "a", "option1", "choice_a"]),
            option_b=first_value(row, ["option_b", "b", "option2", "choice_b"]),
            option_c=first_value(row, ["option_c", "c", "option3", "choice_c"]),
            option_d=first_value(row, ["option_d", "d", "option4", "choice_d"]),
            answer=first_value(row, ["answer", "ans", "correct_answer", "correct"]),
        )

        if item.question:
            parsed_rows.append(item)

    return parsed_rows


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


def draw_centered_text(draw, text, font, box, fill, line_gap=10):
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
    draw.rounded_rectangle((40, 170, width - 40, height - 170), radius=30, fill=(249, 245, 235), outline=(255, 196, 85), width=4)
    draw_centered_text(draw, text, get_font(52, True), (70, 300, width - 70, 700), (25, 25, 25))
    draw_centered_text(draw, "Answer before the timer ends", get_font(28, False), (70, 720, width - 70, 860), (90, 90, 90))
    path = work_dir / "intro.png"
    img.save(path, quality=90)
    return path


def make_outro_screen(text: str, width: int, height: int, work_dir: Path) -> Path:
    img = Image.new("RGB", (width, height), (247, 242, 230))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((40, 170, width - 40, height - 170), radius=30, fill=(255, 255, 255), outline=(18, 88, 104), width=4)
    draw_centered_text(draw, text, get_font(46, True), (70, 320, width - 70, 700), (18, 88, 104))
    draw_centered_text(draw, "Video ready for download", get_font(26, False), (70, 720, width - 70, 860), (80, 80, 80))
    path = work_dir / "outro.png"
    img.save(path, quality=90)
    return path


def make_question_images(idx: int, row: QuizRow, width: int, height: int, watermark: str, work_dir: Path):
    def render(include_answer: bool):
        img = Image.new("RGB", (width, height), (247, 242, 230))
        draw = ImageDraw.Draw(img)

        title_font = get_font(28, True)
        q_font = get_font(36, True)
        opt_font = get_font(26, False)
        ans_font = get_font(30, True)
        small_font = get_font(18, False)

        draw.rounded_rectangle((28, 28, width - 28, height - 28), radius=28, fill=(255, 252, 246), outline=(18, 88, 104), width=3)
        draw.text((50, 50), f"Question {idx}", fill=(18, 88, 104), font=title_font)

        y = 130
        for line in wrap_text(draw, row.question, q_font, width - 100):
            draw.text((50, y), line, fill=(25, 25, 25), font=q_font)
            y += 48

        y += 16
        options = [row.option_a, row.option_b, row.option_c, row.option_d]
        colors = [(210, 80, 60), (60, 140, 80), (110, 70, 170), (210, 145, 20)]

        for i, opt in enumerate([o for o in options if o.strip()]):
            draw.rounded_rectangle((50, y, width - 50, y + 70), radius=18, fill=(243, 246, 249), outline=colors[i % 4], width=2)
            draw.ellipse((65, y + 16, 100, y + 51), fill=colors[i % 4])
            draw.text((76, y + 15), chr(65 + i), fill=(255, 255, 255), font=get_font(18, True))

            lines = wrap_text(draw, opt, opt_font, width - 170)
            oy = y + 12
            for line in lines[:2]:
                draw.text((120, oy), line, fill=(55, 55, 55), font=opt_font)
                oy += 28

            y += 88

        if include_answer and row.answer.strip():
            ay = height - 150
            draw.rounded_rectangle((50, ay, width - 50, ay + 80), radius=20, fill=(18, 88, 104))
            draw.text((72, ay + 23), f"Answer: {row.answer}", fill=(255, 255, 255), font=ans_font)

        if watermark.strip():
            bbox = draw.textbbox((0, 0), watermark, font=small_font)
            tw = bbox[2] - bbox[0]
            draw.text((width - tw - 35, height - 40), watermark, fill=(125, 125, 125), font=small_font)

        return img

    base_path = work_dir / f"q_{idx:03d}_base.png"
    answer_path = work_dir / f"q_{idx:03d}_answer.png"
    render(False).save(base_path, quality=90)
    render(True).save(answer_path, quality=90)
    return base_path, answer_path


#def run_ffmpeg(cmd):
  #  subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
def run_ffmpeg(cmd):
    logger.info("Running FFmpeg command: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    logger.info("FFmpeg return code: %s", result.returncode)

    if result.stdout:
        logger.info("FFmpeg stdout:\n%s", result.stdout[-4000:])

    if result.stderr:
        logger.error("FFmpeg stderr:\n%s", result.stderr[-8000:])

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed with code {result.returncode}\n{result.stderr[-4000:]}"
        )

def image_to_clip(image_path: Path, duration: int, out_path: Path):
    run_ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-t", str(duration),
        "-vf", "fps=24,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def question_to_clip(base_png: Path, answer_png: Path, duration: int, reveal_after: int, width: int, height: int, out_path: Path):
    fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    countdown_expr = f"%{{eif\\:{duration}-t\\:d}}"

    filter_complex = (
        f"[0:v]scale={width}:{height},fps=24[bg];"
        f"[1:v]scale={width}:{height}[ans];"
        f"[bg][ans]overlay=0:0:enable='gte(t,{reveal_after})',"
        f"drawtext=fontfile={fontfile}:text='{countdown_expr}':"
        f"fontcolor=white:fontsize=42:box=1:boxcolor=black@0.45:boxborderw=10:"
        f"x=(w-text_w)/2:y=80"
    )

    run_ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(base_png),
        "-loop", "1", "-i", str(answer_png),
        "-t", str(duration),
        "-filter_complex", filter_complex,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        str(out_path)
    ])


def concat_clips(clips: List[Path], out_path: Path):
    list_file = out_path.parent / "concat.txt"
    lines = [f"file '{clip.resolve().as_posix()}'" for clip in clips]
    list_file.write_text("
".join(lines) + "
", encoding="utf-8")

    run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path)
    ])

def add_music(video_path: Path, music_path: Path | None, volume: float, out_path: Path):
    if music_path and music_path.exists():
        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex", f"[1:a]volume={volume}[bgm]",
            "-map", "0:v:0",
            "-map", "[bgm]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(out_path)
        ])
    else:
        shutil.copy(video_path, out_path)


def safe_name(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")[:50] or "quiz_video"


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
        body{font-family:Arial,sans-serif;max-width:900px;margin:30px auto;padding:20px;background:#f7f2e9;color:#222}
        textarea,input{width:100%;padding:12px;margin:8px 0 16px;box-sizing:border-box}
        button{padding:14px 20px;background:#125868;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:16px}
        .card{background:#fff;padding:24px;border-radius:14px;box-shadow:0 4px 16px rgba(0,0,0,.08)}
        code{background:#f2f2f2;padding:2px 6px;border-radius:4px}
        label{font-weight:700}
        .hint{font-size:14px;color:#666}
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Quiz Video Generator</h1>
        <p>Paste CSV with headers like <code>question,option_a,option_b,option_c,option_d,answer</code>.</p>
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

          <label>Optional background music</label>
          <input type="file" name="music_file" accept=".mp3,.wav,.m4a,.aac"/>

          <button type="submit">Generate Video</button>
          <p class="hint">On small Render instances, shorter quizzes and smaller audio files are safer.</p>
        </form>
      </div>
    </body>
    </html>
    """


@app.post("/generate", response_class=HTMLResponse)
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

        width, height = 720, 1280

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
                while chunk := await music_file.read(1024 * 1024):
                    f.write(chunk)

        clips = [intro_clip]

        for idx, row in enumerate(quiz_rows, start=1):
            base_png, answer_png = make_question_images(idx, row, width, height, watermark, work_dir)
            clip_path = work_dir / f"clip_{idx:03d}.mp4"
            question_to_clip(base_png, answer_png, 6, 4, width, height, clip_path)
            clips.append(clip_path)

        clips.append(outro_clip)

        merged = work_dir / "merged.mp4"
        concat_clips(clips, merged)

        video_id = uuid.uuid4().hex[:12]
        final_name = f"{safe_name(video_title)}_{video_id}.mp4"
        final_path = OUT_DIR / final_name

        add_music(merged, music_path, 0.12, final_path)

        base_url = str(request.base_url).rstrip("/")
        download_url = f"{base_url}/download/{final_name}"
        direct_url = f"{base_url}/files/{final_name}"

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8"/>
          <meta name="viewport" content="width=device-width,initial-scale=1"/>
          <title>Video Ready</title>
          <style>
            body{{font-family:Arial,sans-serif;max-width:760px;margin:40px auto;padding:20px;background:#f7f2e9;color:#222}}
            .card{{background:#fff;padding:28px;border-radius:14px;box-shadow:0 4px 16px rgba(0,0,0,.08)}}
            .btn{{display:inline-block;padding:14px 20px;background:#125868;color:#fff;text-decoration:none;border-radius:8px;font-weight:700}}
            .muted{{color:#666}}
            code{{background:#f2f2f2;padding:2px 6px;border-radius:4px}}
          </style>
        </head>
        <body>
          <div class="card">
            <h1>Video generated successfully</h1>
            <p><strong>File:</strong> {html.escape(final_name)}</p>
            <p><strong>Questions:</strong> {len(quiz_rows)}</p>
            <p><a class="btn" href="{html.escape(download_url)}">Download Video</a></p>
            <p><a href="{html.escape(direct_url)}">Direct file link</a></p>
            <p class="muted">Files stored on Render local disk may disappear after restart or redeploy unless you attach a persistent disk.</p>
            <p><a href="/">Create another video</a></p>
          </div>
        </body>
        </html>
        """

    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="FFmpeg failed while generating the video")
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
