import os
import shutil
import asyncio
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

origins = [
    "https://ptsel-frontend.onrender.com",  # your frontend
    "http://localhost:5173",                # optional, local testing
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------  STORAGE  ----------
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# auto-cleanup every 3 days
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                os.remove(path)

# ----------  ROUTES  ----------
@app.get("/")
def home():
    return {"status": "PTSEL Clipper running and optimized!"}

@app.post("/clip")
async def clip_video(file: UploadFile = File(...),
                     start: str = Form(...),
                     end: str = Form(...)):

    auto_cleanup()

    input_path = os.path.join(UPLOAD_DIR, file.filename)
    output_path = os.path.join(UPLOAD_DIR, f"trimmed_{file.filename}")

    # Save upload
    with open(input_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Run FFmpeg
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ss", start, "-to", end,
        "-c", "copy", output_path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()

    return FileResponse(output_path, media_type="video/mp4",
                        filename=f"trimmed_{file.filename}")

@app.post("/clip_link")
async def clip_from_link(request: Request):
    data = await request.json()
    url = data.get("url")
    start = data.get("start")
    end = data.get("end")

    auto_cleanup()
    temp_in = os.path.join(UPLOAD_DIR, "temp_download.mp4")
    temp_out = os.path.join(UPLOAD_DIR, "trimmed_from_link.mp4")

    # download video via yt-dlp
    await asyncio.create_subprocess_exec(
        "yt-dlp", "-o", temp_in, url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    # trim downloaded video
    cmd = [
        "ffmpeg", "-y",
        "-i", temp_in,
        "-ss", start, "-to", end,
        "-c", "copy", temp_out
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()

    return FileResponse(temp_out, media_type="video/mp4",
                        filename="trimmed_from_link.mp4")

@app.post("/whisper")
async def transcribe(file: UploadFile = File(...)):
    auto_cleanup()
    path = os.path.join(UPLOAD_DIR, file.filename)
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    result = subprocess.run(
        ["whisper", path, "--model", "base", "--language", "en"],
        capture_output=True, text=True
    )
    return JSONResponse({"transcript": result.stdout or "Done."})

