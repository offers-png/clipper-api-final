import os
import shutil
import asyncio
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ✅ Initialize FastAPI app
app = FastAPI()

# ✅ Allow frontend connections
origins = [
    "https://ptsel-frontend.onrender.com",
    "http://localhost:5173"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Persistent upload directory (Render supports /data)
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ✅ Periodic cleanup for files older than 3 days
def auto_cleanup():
    now = datetime.now()
    deleted = 0
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            try:
                if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                    os.remove(path)
                    deleted += 1
            except Exception as e:
                print(f"Cleanup failed for {path}: {e}")
    if deleted:
        print(f"🧹 Auto-cleanup complete: {deleted} old files removed.")


@app.on_event("startup")
async def startup_event():
    # Run cleanup in the background once at startup
    asyncio.create_task(asyncio.to_thread(auto_cleanup))


@app.get("/")
def home():
    """Simple health check endpoint."""
    return {"status": "✅ PTSEL Clipper API is live and ready!"}


@app.post("/clip")
async def clip_video(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    """Trim video using FFmpeg with copy codec (fast and no re-encoding)."""
    try:
        # ✅ Validate input times
        start, end = start.strip(), end.strip()
        if not start or not end:
            return JSONResponse({"error": "Start and end times are required."}, status_code=400)

        # ✅ Save uploaded video to disk
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # ✅ Output file path
        base, ext = os.path.splitext(file.filename)
        output_filename = f"{base}_trimmed{ext}"
        output_path = os.path.join(UPLOAD_DIR, output_filename)

        # ✅ Run FFmpeg trim (copy stream = super fast)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", start, "-to", end,
            "-i", input_path,
            "-c", "copy",
            "-y", output_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # ✅ Handle FFmpeg failure
        if result.returncode != 0:
            print("❌ FFmpeg stderr:", result.stderr)
            return JSONResponse({"error": "FFmpeg failed to trim video."}, status_code=500)

        # ✅ Ensure output was created
        if not os.path.exists(output_path):
            return JSONResponse({"error": "Output file not created."}, status_code=500)

        # ✅ Return trimmed video
        return FileResponse(output_path, filename=output_filename, media_type="video/mp4")

    except Exception as e:
        print(f"❌ Trimming error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
