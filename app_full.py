import os
import shutil
import asyncio
import tempfile
import subprocess
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

# ✅ Initialize FastAPI + OpenAI
app = FastAPI()
client = OpenAI()

# ✅ Allow frontend connections (both services + localhost)
origins = [
    "https://ptsel-frontend.onrender.com",
    "https://clipper-frontend.onrender.com",   # <— your live frontend
    "https://clipper-api-final-1.onrender.com",
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
    asyncio.create_task(asyncio.to_thread(auto_cleanup))

# ============================================================
# ✅ HEALTH CHECK (for frontend connection)
# ============================================================
@app.get("/api/health")
def health():
    return {"ok": True, "message": "Backend is alive and ready"}

# ============================================================
# ✅ ROOT ROUTE
# ============================================================
@app.get("/")
def home():
    return {"status": "✅ PTSEL Clipper + Whisper API is live and ready!"}

# ============================================================
# 🎬 CLIP ENDPOINT
# ============================================================
@app.post("/clip")
async def clip_video(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    try:
        start, end = start.strip(), end.strip()
        if not start or not end:
            return JSONResponse({"error": "Start and end times required."}, status_code=400)

        # Save uploaded file
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        base, _ = os.path.splitext(file.filename)
        output_path = os.path.join(UPLOAD_DIR, f"{base}_trimmed.mp4")

        # ✅ Always output MP4 (no WebM codec restrictions)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", start, "-to", end,
            "-i", input_path,
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "192k",
            "-y", output_path
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1800)

        if result.returncode != 0 or not os.path.exists(output_path):
            print("❌ FFmpeg stderr:", result.stderr)
            return JSONResponse({"error": f"FFmpeg failed: {result.stderr}"}, status_code=500)

        return FileResponse(output_path, filename=f"{base}_trimmed.mp4", media_type="video/mp4")

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "⏱️ FFmpeg timed out while processing large video."}, status_code=504)

    except Exception as e:
        print(f"❌ Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ============================================================
# 🎙️ WHISPER ENDPOINT
# ============================================================
@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(None), url: str = Form(None)):
    try:
        tmp_path = None
        audio_wav = None
        audio_mp3 = None
        os.makedirs("/tmp", exist_ok=True)

        # ✅ Save uploaded file
        if file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm", dir="/tmp") as tmp:
                tmp.write(await file.read())
                tmp_path = tmp.name

        # ✅ OR download from URL
        elif url:
            response = requests.get(url, stream=True, timeout=60)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm", dir="/tmp") as tmp:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    tmp.write(chunk)
                tmp_path = tmp.name
        else:
            return JSONResponse({"error": "No file or URL provided."}, status_code=400)

        # ✅ Step 1: Decode to WAV (handles WebM/Opus)
        audio_wav = tmp_path.rsplit(".", 1)[0] + ".wav"
        decode_cmd = [
            "ffmpeg", "-y",
            "-i", tmp_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "2",
            audio_wav
        ]
        subprocess.run(decode_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # ✅ Step 2: Encode to MP3 (for Whisper)
        audio_mp3 = audio_wav.rsplit(".", 1)[0] + ".mp3"
        encode_cmd = [
            "ffmpeg", "-y",
            "-i", audio_wav,
            "-codec:a", "libmp3lame",
            "-b:a", "192k",
            audio_mp3
        ]
        result = subprocess.run(encode_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0 or not os.path.exists(audio_mp3):
            print("❌ FFmpeg stderr:", result.stderr)
            raise Exception("FFmpeg failed to create audio file")

        # ✅ Send to Whisper
        with open(audio_mp3, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )

        # ✅ Clean up
        for path in [tmp_path, audio_wav, audio_mp3]:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

        # ✅ Return transcript
        text_output = transcript.strip() if transcript else ""
        if not text_output:
            return JSONResponse({"text": "(no text found — maybe silent or unreadable audio)"})

        return JSONResponse({"text": text_output})

    except Exception as e:
        print(f"❌ Error during transcription: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
