from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import subprocess
import os
import uuid
import yt_dlp

# --- Initialize app ---
app = FastAPI()

# --- Enable CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
OUTPUT_DIR = "clips"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


# --- Time parser ---
def parse_time(t: str) -> float:
    """Convert flexible time formats into seconds (supports 5, 00:05, 00:00:05, etc.)"""
    try:
        if not t:
            raise ValueError("Empty time string")

        t = str(t).strip().replace("string", "").replace("'", "").replace('"', "")

        if ":" not in t:
            return float(t)

        parts = [float(p) for p in t.split(":") if p.strip() != ""]
        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid time format: {t}")


@app.post("/trim")
async def trim_video(
    file: UploadFile = File(None),
    url: str = Form(""),
    start: str = Form("0"),
    end: str = Form("60"),
):
    try:
        start_sec = parse_time(start)
        end_sec = parse_time(end)
        if end_sec <= start_sec:
            raise HTTPException(status_code=400, detail="End time must be greater than start time")

        input_path = None

        # --- Handle YouTube URL ---
        if url:
            ydl_opts = {
                "outtmpl": f"{UPLOAD_DIR}/%(id)s.%(ext)s",
                "quiet": True,
                "format": "best[ext=mp4]/best",
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                input_path = ydl.prepare_filename(info)
        elif file:
            input_filename = f"{uuid.uuid4()}_{file.filename}"
            input_path = os.path.join(UPLOAD_DIR, input_filename)
            with open(input_path, "wb") as f:
                f.write(await file.read())
        else:
            raise HTTPException(status_code=400, detail="No file or YouTube URL provided")

        output_filename = f"clip_{uuid.uuid4()}.mp4"
        output_path = os.path.join(OUTPUT_DIR, output_filename)

        # --- Trim with ffmpeg ---
        cmd = [
            "ffmpeg",
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-i", input_path,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-strict", "experimental",
            output_path,
            "-y"
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        size_bytes = os.path.getsize(output_path)

        return JSONResponse({
            "download_url": f"/download/{output_filename}",
            "size_bytes": size_bytes
        })

    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"FFmpeg failed: {e.stderr.decode()[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download/{filename}")
async def download_clip(filename: str):
    file_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)
