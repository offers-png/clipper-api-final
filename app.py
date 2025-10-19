import os
import shutil
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
import yt_dlp

# ✅ Setup
app = FastAPI()

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

UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ✅ Clean old files
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                os.remove(path)

# ✅ Whisper Client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.get("/")
def home():
    return {"status": "PTSEL Clipper backend combined and running ✅"}

# ✅ 1. Upload + Trim Video
@app.post("/clip")
async def clip_video(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # ensure start and end are valid
    if not start or not end:
        return JSONResponse({"error": "Missing start or end time"}, status_code=400)

    output_path = os.path.join(UPLOAD_DIR, f"trimmed_{file.filename}")
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", start,
        "-to", end,
        "-i", file_path,
        "-c:v", "libx264",
        "-c:a", "aac",
        "-preset", "fast",
        output_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return JSONResponse({"error": result.stderr}, status_code=500)

    return FileResponse(output_path, filename=f"trimmed_{file.filename}")

# ✅ 2. Clip from YouTube Link
@app.post("/clip_link")
async def clip_youtube(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        yt_path = os.path.join(UPLOAD_DIR, "yt_source.mp4")
        ydl_opts = {"outtmpl": yt_path, "format": "mp4"}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        return JSONResponse({"error": f"Download failed: {str(e)}"}, status_code=500)

    trimmed_path = os.path.join(UPLOAD_DIR, "yt_trimmed.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", start,
        "-to", end,
        "-i", yt_path,
        "-c:v", "libx264",
        "-c:a", "aac",
        trimmed_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return JSONResponse({"error": result.stderr}, status_code=500)

    return FileResponse(trimmed_path, filename="yt_trimmed.mp4")

# ✅ 3. Whisper Transcription (audio or video)
@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    try:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=audio_file
            )
        return {"text": transcript.text}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
