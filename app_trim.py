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
    try:
        start, end = start.strip(), end.strip()
        if not start or not end:
            return JSONResponse({"error": "Start and end times required."}, status_code=400)

        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        base, ext = os.path.splitext(file.filename)
        output_path = os.path.join(UPLOAD_DIR, f"{base}_trimmed{ext}")

        # ✅ Robust large-file FFmpeg command (forces accurate seeking)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", start, "-to", end,
            "-i", input_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            "-y", output_path
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0 or not os.path.exists(output_path):
            return JSONResponse({"error": f"FFmpeg failed: {result.stderr}"}, status_code=500)

        return FileResponse(output_path, filename=f"{base}_trimmed{ext}", media_type="video/mp4")

    except Exception as e:
        print(f"❌ Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

