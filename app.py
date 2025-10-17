import os
import time
import threading
import subprocess
import sys
from fastapi import FastAPI, File, Form, UploadFile, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, FileResponse

# ======================================================
# STABLE PTSEL CLIPPER (FULL VERSION)
# Supports large videos up to 5GB, persistent disk, auto-cleaner
# ======================================================

# Expand recursion + file limits
sys.setrecursionlimit(10000)

# Create FastAPI app
app = FastAPI(title="PTSEL Clipper", version="2.0")

# Enable compression & big uploads
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Allow frontend connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================================================
# FOLDER SETUP (STABLE PERSISTENT DISK)
# ======================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = "/data/uploads"
CLIP_DIR = "/data/clips"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLIP_DIR, exist_ok=True)

# ======================================================
# AUTO CLEAN OLD FILES (EVERY 24 HOURS)
# ======================================================
def auto_clean():
    while True:
        try:
            now = time.time()
            for folder in [UPLOAD_DIR, CLIP_DIR]:
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    if os.path.isfile(path) and now - os.path.getmtime(path) > 24 * 3600:
                        os.remove(path)
                        print(f"🧹 Deleted old file: {path}")
        except Exception as e:
            print(f"Cleanup error: {e}")
        time.sleep(3600)  # check hourly

threading.Thread(target=auto_clean, daemon=True).start()

# ======================================================
# ROOT ROUTE
# ======================================================
@app.get("/")
async def root():
    return {"message": "PTSEL Clipper API is live and stable 🚀"}

# ======================================================
# MAIN CLIPPER ENDPOINT
# ======================================================
@app.post("/trim")
async def trim_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(None),
    url: str = Form(None),
    start: str = Form(...),
    end: str = Form(...)
):
    """
    Handles trimming for uploaded videos or YouTube URLs.
    Supports up to 5GB input files.
    """

    try:
        timestamp = int(time.time())
        input_path = os.path.join(UPLOAD_DIR, f"input_{timestamp}.mp4")
        output_path = os.path.join(CLIP_DIR, f"output_{timestamp}.mp4")

        # ======================================================
        # Save uploaded file
        # ======================================================
        if file:
            print(f"⬆️ Uploading file: {file.filename}")
            with open(input_path, "wb") as f:
                f.write(await file.read())

        # ======================================================
        # Download from YouTube
        # ======================================================
        elif url:
            print(f"📥 Downloading from YouTube: {url}")
            os.system(f"yt-dlp -f mp4 {url} -o {input_path}")
        else:
            raise HTTPException(status_code=400, detail="No file or URL provided.")

        # ======================================================
        # FFmpeg Command (optimized for fast cutting)
        # ======================================================
        cmd = [
            "ffmpeg", "-y",
            "-ss", start,
            "-to", end,
            "-i", input_path,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            output_path
        ]

        def run_ffmpeg():
            try:
                print(f"🎬 Trimming started: {input_path}")
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print(f"✅ Trim complete: {output_path}")
            except Exception as e:
                print(f"❌ FFmpeg failed: {e}")

        # Run in background so frontend doesn’t freeze
        background_tasks.add_task(run_ffmpeg)

        # Return download link
        return JSONResponse({
            "message": "Processing started in background.",
            "download_url": f"https://clipper-api-final-1.onrender.com/clips/output_{timestamp}.mp4"
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ======================================================
# CLIP DOWNLOAD ROUTE
# ======================================================
@app.get("/clips/{filename}")
async def get_clip(filename: str):
    clip_path = os.path.join(CLIP_DIR, filename)
    if os.path.exists(clip_path):
        return FileResponse(clip_path, media_type="video/mp4", filename=filename)
    raise HTTPException(status_code=404, detail="Clip not found")
