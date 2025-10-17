from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, File, Form, UploadFile, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import subprocess
import os
import time
import threading

# ======================================================
# SETUP
# ======================================================

app = FastAPI()

# Allow frontend connection (local and hosted)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # change later to your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================================================
# FIXED PATHS (SAFE FOR RENDER)
# ======================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
CLIP_DIR = os.path.join(BASE_DIR, "clips")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLIP_DIR, exist_ok=True)

# ======================================================
# AUTO CLEAN OLD FILES
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
        time.sleep(3600)  # every hour

threading.Thread(target=auto_clean, daemon=True).start()

# ======================================================
# ROOT ROUTE
# ======================================================

@app.get("/")
async def root():
    return {"message": "PTSEL Clipper API is live and running 🚀"}

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
    Trims an uploaded video or YouTube video link between start and end time.
    Saves output and returns a ready download link.
    """

    try:
        timestamp = int(time.time())
        input_path = os.path.join(UPLOAD_DIR, f"input_{timestamp}.mp4")
        output_path = os.path.join(CLIP_DIR, f"output_{timestamp}.mp4")

        # Save uploaded file
        if file:
            with open(input_path, "wb") as f:
                f.write(await file.read())

        # Download YouTube video if URL provided
        elif url:
            os.system(f"yt-dlp -f mp4 {url} -o {input_path}")
        else:
            raise HTTPException(status_code=400, detail="No file or URL provided.")

        # FFmpeg command
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
                print(f"🎬 Starting trim: {input_path}")
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print(f"✅ Trim complete: {output_path}")
            except Exception as e:
                print(f"FFmpeg failed: {e}")

        # Run FFmpeg in background
        background_tasks.add_task(run_ffmpeg)

        # Return download link
        return JSONResponse({
            "message": "Processing started in background.",
            "download_url": f"https://clipper-api-final.onrender.com/clips/output_{timestamp}.mp4"
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ======================================================
# DOWNLOAD CLIP
# ======================================================

@app.get("/clips/{filename}")
async def get_clip(filename: str):
    clip_path = os.path.join(CLIP_DIR, filename)
    if os.path.exists(clip_path):
        return FileResponse(clip_path, media_type="video/mp4", filename=filename)
    raise HTTPException(status_code=404, detail="Clip not found")
