# =========================
# app.py  (FastAPI backend)
# =========================
import os
import uuid
import shutil
import tempfile
import subprocess
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# Optional: yt_dlp for YouTube links
import yt_dlp

APP_DIR = Path(__file__).parent.resolve()
STATIC_DIR = APP_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
TMP_DIR = Path("/tmp/ptsel")
TMP_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="PTSEL Clipper")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve index.html at /
@app.get("/", response_class=HTMLResponse)
def root():
    index_path = APP_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>PTSEL Clipper</h1><p>index.html missing.</p>", status_code=200)
    return index_path.read_text(encoding="utf-8")


def _check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(["ffprobe", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except Exception:
        raise HTTPException(status_code=500, detail="FFmpeg is not installed on the server.")


def parse_hhmmss(ts: str) -> int:
    """
    Accepts: "SS", "MM:SS", "HH:MM:SS"
    Returns: total seconds (int)
    """
    if not ts:
        raise ValueError("Empty timestamp")
    parts = ts.strip().split(":")
    parts = [p.strip() for p in parts]
    if len(parts) == 1:
        return int(float(parts[0]))
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + int(float(s))
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + int(float(s))
    raise ValueError("Invalid time format")


def ffmpeg_trim(input_path: Path, start_s: int, end_s: int, out_path: Path):
    """
    Accurate trim via re-encode (libx264/aac) for clean frame boundaries.
    """
    duration = end_s - start_s
    if duration <= 0:
        raise HTTPException(status_code=400, detail="End time must be greater than start time.")

    cmd = [
        "ffmpeg",
        "-hide_banner", "-y",
        "-ss", str(start_s),
        "-i", str(input_path),
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(out_path),
    ]
    run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if run.returncode != 0 or not out_path.exists():
        raise HTTPException(status_code=500, detail=f"FFmpeg failed to trim the clip. {run.stderr.decode('utf-8')[:4000]}")


def download_youtube(url: str, temp_dir: Path) -> Path:
    """
    Downloads best video+audio and returns the file path.
    """
    outtmpl = str(temp_dir / "input.%(ext)s")
    ydl_opts = {
        "format": "bv*+ba/b",  # best video+audio, else best
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        # If not mp4 (rare), remux to mp4 for consistency
        if path.suffix.lower() != ".mp4":
            mp4_path = path.with_suffix(".mp4")
            remux = subprocess.run(
                ["ffmpeg", "-y", "-i", str(path), "-c", "copy", str(mp4_path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            if remux.returncode == 0 and mp4_path.exists():
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                path = mp4_path
        return path


def schedule_cleanup(background: BackgroundTasks, *paths: Path):
    for p in paths:
        background.add_task(lambda x=p: x.exists() and x.unlink(missing_ok=True))


@app.post("/trim")
async def trim_video(
    background: BackgroundTasks,
    file: Optional[UploadFile] = File(default=None),
    url: Optional[str] = Form(default=None),
    start: str = Form(..., description="Start time (SS | MM:SS | HH:MM:SS)"),
    end: str = Form(..., description="End time (SS | MM:SS | HH:MM:SS)"),
):
    """
    One endpoint for both:
      - multipart/form-data with 'file' (UploadFile)
      - OR multipart/form-data with 'url' (YouTube link)
    and 'start', 'end' times.
    Enforces max 60 minutes clip.
    Returns a download URL to the trimmed mp4.
    """
    _check_ffmpeg()

    if not file and not url:
        raise HTTPException(status_code=400, detail="Provide either an uploaded file or a YouTube URL.")

    try:
        start_s = parse_hhmmss(start)
        end_s = parse_hhmmss(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid time format. Use SS, MM:SS, or HH:MM:SS.")

    clip_len = end_s - start_s
    if clip_len <= 0:
        raise HTTPException(status_code=400, detail="End must be greater than Start.")
    if clip_len > 3600:
        raise HTTPException(status_code=400, detail="Maximum clip length is 60 minutes.")

    work_dir = TMP_DIR / str(uuid.uuid4())
    work_dir.mkdir(parents=True, exist_ok=True)

    input_path: Path
    try:
        if url:
            # Download from YouTube
            input_path = download_youtube(url, work_dir)
            if not input_path.exists():
                raise HTTPException(status_code=500, detail="Failed to download source video.")
        else:
            # Save uploaded file
            if file.filename is None:
                raise HTTPException(status_code=400, detail="Invalid uploaded file.")
            input_name = "upload_" + Path(file.filename).name
            input_path = work_dir / input_name
            with input_path.open("wb") as f:
                shutil.copyfileobj(file.file, f)

        out_id = str(uuid.uuid4())
        out_path = work_dir / f"{out_id}.mp4"

        # Do the trim
        ffmpeg_trim(input_path, start_s, end_s, out_path)

        # Return a direct download route
        download_url = f"/download/{out_id}"
        size_bytes = out_path.stat().st_size

        # Cleanup input after response is sent (keep output until downloaded)
        schedule_cleanup(background, input_path)

        return {
            "ok": True,
            "download_url": download_url,
            "size_bytes": size_bytes,
            "filename": f"{out_id}.mp4",
            "message": "Clip ready.",
        }
    except HTTPException:
        # propagate
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}") from e


@app.get("/download/{clip_id}")
def download_clip(clip_id: str, background: BackgroundTasks):
    """
    Serves the trimmed mp4 once. File is deleted after response completes.
    """
    # Find the file anywhere under TMP_DIR
    matches = list(TMP_DIR.glob(f"**/{clip_id}.mp4"))
    if not matches:
        raise HTTPException(status_code=404, detail="Clip not found or already downloaded.")
    file_path = matches[0]

    # After we serve, schedule deletion of the entire work dir
    work_dir = file_path.parent

    def _cleanup_dir(p: Path):
        try:
            for c in p.glob("*"):
                c.unlink(missing_ok=True)
            p.rmdir()
        except Exception:
            pass

    background.add_task(_cleanup_dir, work_dir)
    return FileResponse(
        path=str(file_path),
        filename=f"clip_{clip_id}.mp4",
        media_type="video/mp4"
    )


# =========================
# index.html (Frontend UI)
# =========================
# Place this file next to app.py
# If you already have a frontend, you can just re-use the <form> + JS below.
