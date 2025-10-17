from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, File, Form, UploadFile, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import subprocess
import os
import time
import threading

# Create the app
app = FastAPI()

# Allow frontend to connect (local files & hosted)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can later restrict this to your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Use writable paths inside Render project directory
base_dir = "/opt/render/project/src/data"
clip_dir = os.path.join(base_dir, "clips")
upload_dir = os.path.join(base_dir, "uploads")

os.makedirs(upload_dir, exist_ok=True)
os.makedirs(clip_dir, exist_ok=True)

# ==============================================================
# 🔄 Auto-clean: delete clips older than 24 hours
# ==============================================================
def auto_clean_clips():
    while True:
        try:
            now = time.time()
            for filename in os.listdir(clip_dir):
                path = os.path.join(clip_dir, filename)
                if os.path.isfile(path) and now - os.path.getmtime(path) > 24 * 3600:
                    os.remove(path)
                    print(f"🧹 Deleted old clip: {filename}")
        except Exception as e:
            print(f"Cleanup error: {e}")
        time.sleep(3600)  # check every hour

# Start cleaner thread
threading.Thread(target=auto_clean_clips, daemon=True).start()

# ==============================================================
# Routes
# ==============================================================

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
    try:
        timestamp = int(time.time())
        input_path = f"{upload_dir}/input_{timestamp}.mp4"
        output_path = f"{clip_dir}/output_{timestamp}.mp4"

        if file:
            with open(input_path, "wb") as f:
                f.write(await file.read())
        elif url:
            os.system(f"yt-dlp -f mp4 {url} -o {input_path}")
        else:
            raise HTTPException(status_code=400, detail="No file or URL provided.")

        cmd = [
            "ffmpeg", "-y", "-ss", start, "-to", end, "-i", input_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            output_path
        ]

        def run_ffmpeg():
            try:
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print(f"✅ Clip finished: {output_path}")
            except Exception as e:
                print(f"FFmpeg failed: {e}")

        background_tasks.add_task(run_ffmpeg)

        return JSONResponse({
            "message": "Processing started in background.",
            "download_url": f"/clips/output_{timestamp}.mp4"
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/clips/{filename}")
async def get_clip(filename: str):
    clip_path = os.path.join(clip_dir, filename)
    if os.path.exists(clip_path):
        return FileResponse(clip_path, media_type="video/mp4", filename=filename)
    raise HTTPException(status_code=404, detail="Clip not found")
