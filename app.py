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
        # Create temp folder
        video_path = os.path.join(UPLOAD_DIR, "downloaded_video.mp4")

        # Download video using yt-dlp
        ytdlp_cmd = [
            "yt-dlp",
            "-f", "mp4",
            "-o", video_path,
            url
        ]
        subprocess.run(ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if not os.path.exists(video_path):
            return JSONResponse({"error": "Failed to download video"}, status_code=500)

        # Output trimmed file
        output_filename = f"trimmed_from_url.mp4"
        output_path = os.path.join(UPLOAD_DIR, output_filename)

        # FFmpeg trim
        cmd = [
            "ffmpeg",
            "-ss", start,
            "-to", end,
            "-i", video_path,
            "-c", "copy",
            output_path,
            "-y"
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0:
            return JSONResponse({"error": f"FFmpeg failed: {result.stderr}"}, status_code=500)

        return FileResponse(output_path, filename=output_filename)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
