import os
import shutil
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
import yt_dlp

# ✅ Create app
app = FastAPI()

# ✅ Allow frontend
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

# ✅ Upload folder
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ✅ Cleanup old files
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                os.remove(path)

# ✅ OpenAI client (use your environment variable)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.get("/")
def home():
    return {"status": "PTSEL Clipper backend running ✅"}

# ✅ 1. Upload and trim local video
@app.post("/clip")
async def clip_video(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    output_path = os.path.join(UPLOAD_DIR, f"trimmed_{file.filename}")
    cmd = ["ffmpeg", "-ss", start, "-to", end, "-i", file_path, "-c", "copy", output_path, "-y"]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return FileResponse(output_path, filename=f"trimmed_{file.filename}")

# ✅ 2. Download from YouTube and trim
@app.post("/clip_link")
async def clip_youtube(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    output_path = os.path.join(UPLOAD_DIR, "yt_download.mp4")

    # Download video using yt-dlp
    ydl_opts = {"outtmpl": output_path, "format": "mp4"}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        return JSONResponse({"error": f"Failed to download: {str(e)}"}, status_code=500)

    # Trim it
    trimmed_path = os.path.join(UPLOAD_DIR, "yt_trimmed.mp4")
    cmd = ["ffmpeg", "-ss", start, "-to", end, "-i", output_path, "-c", "copy", trimmed_path, "-y"]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return FileResponse(trimmed_path, filename="yt_trimmed.mp4")

# ✅ 3. Transcribe audio/video (AI Whisper)
@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio_file
        )
    return {"text": transcript.text}
