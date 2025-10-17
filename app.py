from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, File, Form, UploadFile, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import subprocess
import os
import time

# Create the app
app = FastAPI()

# Allow frontend to connect (local files & hosted)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # you can later restrict this to your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Use persistent storage path on Render (doesn't reset on restart)
clip_dir = "/var/data/clips"
upload_dir = "/var/data/uploads"

os.makedirs(upload_dir, exist_ok=True)
os.makedirs(clip_dir, exist_ok=True)


@app.get("/")
async def root():
    return {"message": "PTSEL Clipper API running successfully 🚀"}


@app.post("/trim")
async def trim_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(None),
    url: str = Form(None),
    start: str = Form(...),
    end: str = Form(...)
):
    """
    Trims or downloads video, runs ffmpeg in background, and returns a download URL.
    Works with uploaded files or YouTube URLs.
    """

    try:
        # Create unique file names (use persistent paths)
        timestamp = int(time.time())
        input_path = f"{upload_dir}/input_{timestamp}.mp4"
        output_path = f"{clip_dir}/output_{timestamp}.mp4"

        # Save uploaded video
        if file:
            with open(input_path, "wb") as f:
                f.write(await file.read())

        # Or download YouTube video if URL provided
        elif url:
            os.system(f"yt-dlp -f mp4 {url} -o {input_path}")
        else:
            raise HTTPException(status_code=400, detail="No file or URL provided.")

        # FFmpeg command optimized for larger videos (safe, fast, stable)
        cmd = [
            "ffmpeg",
            "-y",                  # overwrite if exists
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

        # Function to run ffmpeg safely in background
        def run_ffmpeg():
            try:
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except Exception as e:
                print(f"FFmpeg failed: {e}")

        # Run ffmpeg in background (prevents Render timeout)
        background_tasks.add_task(run_ffmpeg)

        # Return link immediately
        return JSONResponse({
            "message": "Processing started in background.",
            "download_url": f"/clips/output_{timestamp}.mp4"
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ✅ Serve finished clips from persistent storage
@app.get("/clips/{filename}")
async def get_clip(filename: str):
    clip_path = os.path.join(clip_dir, filename)
    if os.path.exists(clip_path):
        return FileResponse(clip_path, media_type="video/mp4", filename=filename)
    raise HTTPException(status_code=404, detail="Clip not found")
