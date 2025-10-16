from fastapi import FastAPI, File, Form, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
import uuid, subprocess, shutil, os

app = FastAPI(title="PTSEL Clipper", version="1.0.1")

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# Setup Temporary Directory
# =========================================================
TMP_DIR = Path("/tmp/clipper")
TMP_DIR.mkdir(exist_ok=True, parents=True)


# =========================================================
# Convert time strings (HH:MM:SS, MM:SS, or SS) → seconds
# =========================================================
def parse_time(t: str) -> float:
    """Convert flexible time format into seconds."""
    try:
        t = str(t).strip().lower().replace("string", "").replace('"', '').replace("'", "")
        parts = [float(p) for p in t.split(":") if p.strip()]
        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            raise ValueError("Invalid format")
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid time value: {t}")


# =========================================================
# Trim video with FFmpeg
# =========================================================
def ffmpeg_trim(input_path: Path, start_s: float, end_s: float, output_path: Path):
    """Trim a video between start and end timestamps."""
    try:
        if shutil.which("ffmpeg") is None:
            raise HTTPException(status_code=500, detail="FFmpeg not found in environment.")
        duration = end_s - start_s
        if duration <= 0:
            raise HTTPException(status_code=400, detail="End must be greater than start.")
        cmd = [
            "ffmpeg",
            "-ss", str(start_s),
            "-i", str(input_path),
            "-t", str(duration),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-b:a", "128k",
            "-preset", "ultrafast",
            "-y",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {result.stderr[:400]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# Helper cleanup
# =========================================================
def schedule_cleanup(bg: BackgroundTasks, *paths: Path):
    """Delete temporary files after response."""
    def _delete():
        for p in paths:
            try:
                if p.is_file():
                    p.unlink(missing_ok=True)
                elif p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
            except:
                pass
    bg.add_task(_delete)


# =========================================================
# Root endpoint
# =========================================================
@app.get("/")
def root():
    return {"ok": True, "message": "PTSEL Clipper API is live"}


# =========================================================
# Main trim route
# =========================================================
@app.post("/trim")
async def trim_video(
    background: BackgroundTasks,
    file: UploadFile = File(None),
    url: str = Form(""),
    start: str = Form(...),
    end: str = Form(...)
):
    try:
        start_s = parse_time(start)
        end_s = parse_time(end)

        work_dir = TMP_DIR / str(uuid.uuid4())
        work_dir.mkdir(parents=True, exist_ok=True)

        input_path = work_dir / "input.mp4"

        # Download from YouTube if no file
        if not file and url:
            yt_path = work_dir / "yt.mp4"
            cmd = ["yt-dlp", "-f", "mp4", "-o", str(yt_path), url]
            subprocess.run(cmd, capture_output=True)
            if not yt_path.exists():
                raise HTTPException(status_code=400, detail="Failed to download YouTube video.")
            input_path = yt_path
        elif file:
            with input_path.open("wb") as f:
                shutil.copyfileobj(file.file, f)
        else:
            raise HTTPException(status_code=400, detail="No video input provided.")

        # Output path
        out_id = str(uuid.uuid4())
        out_path = work_dir / f"{out_id}.mp4"

        # Trim
        ffmpeg_trim(input_path, start_s, end_s, out_path)

        if not out_path.exists():
            raise HTTPException(status_code=500, detail="Trim failed: output not found.")

        download_url = f"/download/{out_id}"
        schedule_cleanup(background, work_dir)

        return {
            "ok": True,
            "download_url": download_url,
            "size_bytes": out_path.stat().st_size,
            "filename": f"{out_id}.mp4",
            "message": "Clip ready."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


# =========================================================
# Download route
# =========================================================
@app.get("/download/{clip_id}")
def download_clip(clip_id: str, background: BackgroundTasks):
    matches = list(TMP_DIR.glob(f"**/{clip_id}.mp4"))
    if not matches:
        raise HTTPException(status_code=404, detail="Clip not found.")
    file_path = matches[0]
    work_dir = file_path.parent
    schedule_cleanup(background, work_dir)
    return FileResponse(
        path=str(file_path),
        filename=f"clip_{clip_id}.mp4",
        media_type="video/mp4"
    )

