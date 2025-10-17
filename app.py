import os
import shutil
import asyncio
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware

# Allow frontend to access backend (CORS fix)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ptsel-frontend.onrender.com"],  # You can use ["*"] for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Persistent storage for uploaded chunks
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 🔧 Auto-cleanup: delete files older than 3 days
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                os.remove(path)

@app.get("/")
def home():
    return {"status": "PTSEL Clipper running and optimized!"}

# 🧩 Upload video chunks in parts (for large files)
@app.post("/upload_chunk")
async def upload_chunk(
    chunk: UploadFile = File(...),
    filename: str = Form(...),
    index: int = Form(...),
    total: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...)
):
    temp_dir = os.path.join(UPLOAD_DIR, filename)
    os.makedirs(temp_dir, exist_ok=True)
    chunk_path = os.path.join(temp_dir, f"{index}.part")

    with open(chunk_path, "wb") as f:
        shutil.copyfileobj(chunk.file, f)

    return {"status": "ok", "chunk_index": index}

# 🧠 Merge all chunks and trim asynchronously
@app.post("/merge_chunks")
async def merge_chunks(request: Request):
    try:
        data = await request.json()
        filename = data["filename"]
        start = data["start_time"]
        end = data["end_time"]

        temp_dir = os.path.join(UPLOAD_DIR, filename)
        merged_path = os.path.join(UPLOAD_DIR, filename)
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{filename}")

        # Merge all chunks into one file
        with open(merged_path, "wb") as merged:
            for part in sorted(os.listdir(temp_dir), key=lambda x: int(x.split(".")[0])):
                with open(os.path.join(temp_dir, part), "rb") as f:
                    shutil.copyfileobj(f, merged)

        # 🚀 Run ffmpeg asynchronously (non-blocking + faster)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-hide_banner",
            "-hwaccel", "auto",                # use hardware acceleration if available
            "-ss", start, "-to", end,
            "-accurate_seek",                  # accurate cut
            "-i", merged_path,
            "-c:v", "libx264",                 # re-encode for reliability
            "-preset", "ultrafast",            # prioritize speed
            "-crf", "28",                      # quality vs speed trade-off
            "-c:a", "aac", "-b:a", "128k",
            output_path
        )
        await process.communicate()

        # Delete chunk folder after merging
        shutil.rmtree(temp_dir, ignore_errors=True)

        # Clean old files automatically
        auto_cleanup()

        return JSONResponse({"download_url": f"/download/{os.path.basename(output_path)}"})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# 📥 Download the finished clip
@app.get("/download/{filename}")
async def download_file(filename: str):
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(path, filename=filename)

