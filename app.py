import os
import shutil
import asyncio
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ✅ Create the FastAPI app FIRST
app = FastAPI()

# ✅ CORS (connect backend with frontend)
origins = [
    "https://ptsel-frontend.onrender.com",  # your Render frontend
    "http://localhost:5173",                # optional local testing
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Upload directory setup
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ✅ Auto cleanup every 3 days
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                os.remove(path)

# ✅ Routes
@app.get("/")
def home():
    return {"status": "PTSEL Clipper running and optimized!"}

@app.post("/clip_url")
async def clip_video_url(
    url: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
):
    try:
        # Clean up old files
        for f in os.listdir(UPLOAD_DIR):
            if f.startswith("downloaded_video"):
                os.remove(os.path.join(UPLOAD_DIR, f))

        # --- 1. Download video properly ---
        video_path = os.path.join(UPLOAD_DIR, "downloaded_video.%(ext)s")
        ytdlp_cmd = [
            "yt-dlp",
            "-f", "bestvideo+bestaudio",
            "--merge-output-format", "mp4",
            "-o", video_path,
            url
        ]
        result = subprocess.run(ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            return JSONResponse({"error": f"yt-dlp failed: {result.stderr}"}, status_code=500)

        # Detect actual saved file
        actual_video_path = None
        for f in os.listdir(UPLOAD_DIR):
            if f.startswith("downloaded_video") and f.endswith(".mp4"):
                actual_video_path = os.path.join(UPLOAD_DIR, f)
                break

        if not actual_video_path or os.path.getsize(actual_video_path) < 500000:
            return JSONResponse({"error": "Downloaded file missing or empty."}, status_code=500)

        # --- 2. Trim video (re-encode) ---
        output_filename = "trimmed_from_url.mp4"
        output_path = os.path.join(UPLOAD_DIR, output_filename)
        cmd = [
            "ffmpeg",
            "-ss", start,
            "-to", end,
            "-i", actual_video_path,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-movflags", "+faststart",
            "-y", output_path
        ]
        trim_result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if trim_result.returncode != 0:
            return JSONResponse({"error": f"FFmpeg failed: {trim_result.stderr}"}, status_code=500)

        # --- 3. Return the trimmed file ---
        return FileResponse(output_path, filename=output_filename)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

