import os, shutil, subprocess, yt_dlp, asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

# ========================
# ✅ SETUP
# ========================
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

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ========================
# ✅ AUTO CLEANUP (3 days)
# ========================
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"Cleanup failed: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(asyncio.to_thread(auto_cleanup))

@app.get("/")
def home():
    return {"status": "PTSEL Clipper backend combined and running ✅"}

# ========================
# 🎬 1. Upload + Trim
# ========================
@app.post("/clip")
async def clip_video(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    try:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        output_filename = f"trimmed_{file.filename}"
        output_path = os.path.join(UPLOAD_DIR, output_filename)

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", start, "-to", end, "-i", file_path,
            "-c", "copy", "-y", output_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0:
            return JSONResponse({"error": result.stderr}, status_code=500)
        return FileResponse(output_path, filename=output_filename)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ========================
# 🔗 2. Clip from YouTube URL
# ========================
@app.post("/clip_link")
async def clip_youtube(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        source_path = os.path.join(UPLOAD_DIR, "yt_source.mp4")
        output_path = os.path.join(UPLOAD_DIR, "yt_trimmed.mp4")

        ydl_opts = {"outtmpl": source_path, "format": "mp4"}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", start, "-to", end, "-i", source_path,
            "-c", "copy", "-y", output_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0:
            return JSONResponse({"error": result.stderr}, status_code=500)
        return FileResponse(output_path, filename="yt_trimmed.mp4")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ========================
# 🧠 3. Whisper Transcription
# ========================
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
