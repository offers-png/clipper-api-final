import os
import shutil
import uuid
import subprocess
from pathlib import Path
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="PTSEL Clipper",
    description="Video clipper API with persistent file storage",
    version="1.0.1"
)

# ===============================================================
# Enable CORS for frontend
# ===============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================================================
# Persistent storage directories
# ===============================================================
BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp_clips"
TMP_DIR.mkdir(exist_ok=True)

PERSISTENT_DIR = BASE_DIR / "clips"
PERSISTENT_DIR.mkdir(exist_ok=True)

# ===============================================================
# Time parsing helper (HH:MM:SS, MM:SS, or SS)
# ===============================================================
def parse_time(t: str) -> float:
    try:
        parts = [float(p) for p in t.split(":")]
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

# ===============================================================
# Trim using FFmpeg
# ===============================================================
def ffmpeg_trim(input_path: Path, start: float, end: float, output_path: Path):
    try:
        cmd = [
            "ffmpeg",
            "-ss", str(start),
            "-to", str(end),
            "-i", str(input_path),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-strict", "experimental",
            "-y",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="FFmpeg processing failed")

# ===============================================================
# API routes
# ===============================================================
@app.get("/")
def root():
    return {"message": "PTSEL Clipper API active"}

@app.post("/trim")
async def trim_video(
    file: UploadFile = File(None),
    url: str = Form(""),
    start: str = Form(...),
    end: str = Form(...),
):
    if not file and not url:
        raise HTTPException(status_code=400, detail="Upload a file or provide a URL")

    start_time = parse_time(start)
    end_time = parse_time(end)

    # Temp file
    input_path = TMP_DIR / f"{uuid.uuid4()}.mp4"
    output_path = TMP_DIR / f"{uuid.uuid4()}.mp4"

    # Save uploaded file
    if file:
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    else:
        raise HTTPException(status_code=400, detail="URL trimming not yet supported")

    # Process video
    ffmpeg_trim(input_path, start_time, end_time, output_path)

    # Move to persistent folder (so it's not deleted)
    final_id = str(uuid.uuid4())
    final_path = PERSISTENT_DIR / f"{final_id}.mp4"
    shutil.move(str(output_path), final_path)

    file_size = final_path.stat().st_size

    return JSONResponse(
        {
            "ok": True,
            "download_url": f"/download/{final_id}",
            "size_bytes": file_size,
            "filename": final_path.name,
            "message": "Clip ready!",
        }
    )

@app.get("/download/{clip_id}")
def download_clip(clip_id: str):
    file_path = PERSISTENT_DIR / f"{clip_id}.mp4"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Clip not found.")
    return FileResponse(file_path, media_type="video/mp4", filename=f"{clip_id}.mp4")
