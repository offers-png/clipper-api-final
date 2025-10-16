from fastapi import FastAPI, File, Form, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import shutil
import subprocess
import uuid
import os

app = FastAPI(title="PTSEL Clipper", version="0.1.0")

# ============================================================
# ✅ CORS FIX (Frontend Connection)
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for now (safe for testing)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# Setup Temporary Directory
# ============================================================
TMP_DIR = Path("/tmp/clipper")
TMP_DIR.mkdir(exist_ok=True, parents=True)

# ============================================================
# Helper: Convert Time (HH:MM:SS or MM:SS or SS) to Seconds
# ============================================================
def parse_time(t: str) -> float:
    parts = [float(p) for p in t.split(":")]
    if len(parts) == 1:
        return parts[0]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    else:
        raise ValueError("Invalid time format")

# ============================================================
# Helper: Trim Video Using FFmpeg
# ============================================================
def ffmpeg_trim(input_path: Path, start: str, end: str, output_path: Path):
    try:
        start_s = parse_time(start)
        end_s = parse_time(end)
        if end_s <= start_s:
            raise ValueError("End time must be greater than start time")

        # Run ffmpeg
        cmd = [
            "ffmpeg",
            "-y",  # overwrite output
            "-i", str(input_path),
            "-ss", str(start_s),
            "-to", str(end_s),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output_path),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"FFmpeg failed: {e.stderr.decode()}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# Helper: Schedule File Cleanup
# ============================================================
def schedule_cleanup(background: BackgroundTasks, path: Path):
    def _cleanup(p: Path):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    background.add_task(_cleanup, path)

# ============================================================
# Root Route
# ============================================================
@app.get("/")
def root():
    return {"ok": True, "message": "PTSEL Clipper API is running."}

# ============================================================
# Main Route: /trim
# ============================================================
@app.post("/trim")
async def trim_video(
    background: BackgroundTasks,
    file: UploadFile = File(None),
    url: str = Form(None),
    start: str = Form(...),
    end: str = Form(...),
):
    try:
        work_dir = TMP_DIR / str(uuid.uuid4())
        work_dir.mkdir(parents=True, exist_ok=True)

        # Downloaded or uploaded input path
        input_path = work_dir / "input.mp4"

        # Case 1: YouTube URL (handled by yt-dlp)
        if url and not file:
            import yt_dlp
            ydl_opts = {
                "outtmpl": str(input_path),
                "quiet": True,
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

        # Case 2: Uploaded file
        elif file:
            input_name = "upload_" + Path(file.filename).name
            input_path = work_dir / input_name
            with input_path.open("wb") as f:
                shutil.copyfileobj(file.file, f)

        else:
            raise HTTPException(status_code=400, detail="No input file or URL provided.")

        # Output file path
        out_id = str(uuid.uuid4())
        out_path = work_dir / f"{out_id}.mp4"

        # Do the trim
        ffmpeg_trim(input_path, start, end, out_path)

        # Cleanup input file after response is sent
        schedule_cleanup(background, input_path)

        # Return download info
        size_bytes = out_path.stat().st_size
        download_url = f"/download/{out_id}"
        return {
            "ok": True,
            "download_url": download_url,
            "size_bytes": size_bytes,
            "filename": f"{out_id}.mp4",
            "message": "Clip ready.",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

# ============================================================
# Download Route
# ============================================================
@app.get("/download/{clip_id}")
def download_clip(clip_id: str, background: BackgroundTasks):
    """
    Serves the trimmed mp4 once. File is deleted after response completes.
    """
    matches = list(TMP_DIR.glob(f"**/{clip_id}.mp4"))
    if not matches:
        raise HTTPException(status_code=404, detail="Clip not found or already downloaded.")
    file_path = matches[0]
    work_dir = file_path.parent

    # Clean up work directory after serving file
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
