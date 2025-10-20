import os
import shutil
import subprocess
from datetime import datetime, timedelta
import asyncio
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
from openai import OpenAI

# Initialize app
app = FastAPI()

# ✅ CORS
origins = [
    "https://ptsel-frontend.onrender.com",
    "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Upload directory
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ✅ Auto cleanup every 3 days
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"Cleanup failed for {path}: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(asyncio.to_thread(auto_cleanup))

# ✅ Health check (required for Render)
@app.get("/")
def root():
    return {"status": "Clipper API running on Render ✅"}

# ✅ Helper - ffmpeg trimming
def run_ffmpeg(input_path, start, end, output_path):
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-y", "-ss", start, "-to", end,
        "-i", input_path, "-c", "copy", output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# ✅ Upload + Clip
@app.post("/clip")
async def clip_video(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    try:
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{file.filename}")
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        run_ffmpeg(input_path, start, end, output_path)
        return FileResponse(output_path, media_type="video/mp4", filename=f"trimmed_{file.filename}")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ✅ YouTube URL + Clip
@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
        input_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")
        ydl_opts = {
            "outtmpl": input_path,
            "format": "best[ext=mp4]/mp4",
            "quiet": True,
            "noplaylist": True,
            "nocheckcertificate": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        run_ffmpeg(input_path, start, end, output_path)
        return FileResponse(output_path, media_type="video/mp4", filename=f"trimmed_{video_id}.mp4")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ✅ Whisper transcription
@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
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
