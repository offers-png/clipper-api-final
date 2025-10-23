import os
import shutil
import asyncio
import tempfile
import subprocess
import requests
import json
from datetime import datetime, timedelta
from zipfile import ZipFile
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
    "https://clipper-frontend.onrender.com",
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

# ✅ Cleanup for files older than 3 days
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
# ✅ HEALTH CHECK
# ============================================================
@app.get("/api/health")
def health():
    return {"ok": True, "message": "Backend is alive and ready"}

@app.get("/")
def home():
    return {"status": "✅ PTSEL Clipper + Whisper API is live and ready!"}

# ============================================================
# 🎬 SINGLE CLIP ENDPOINT
# ============================================================
@app.post("/clip")
async def clip_video(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    try:
        start, end = start.strip(), end.strip()
        if not start or not end:
            return JSONResponse({"error": "Start and end times required."}, status_code=400)

        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        base, _ = os.path.splitext(file.filename)
        output_path = os.path.join(UPLOAD_DIR, f"{base}_trimmed.mp4")

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
        return JSONResponse({"error": "⏱️ FFmpeg timed out."}, status_code=504)
    except Exception as e:
        print(f"❌ Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ============================================================
# 🎬 MULTI-CLIP ENDPOINT (5 sections, zipped)
# ============================================================
@app.post("/clip_multi")
async def clip_multi(file: UploadFile = File(...), sections: str = Form(...)):
    try:
        data = json.loads(sections)
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        zip_path = os.path.join(UPLOAD_DIR, "clips_bundle.zip")
        with ZipFile(zip_path, "w") as zipf:
            for idx, sec in enumerate(data, start=1):
                start, end = sec["start"], sec["end"]
                out_name = f"clip_{idx}_{file.filename}.mp4"
                out_path = os.path.join(UPLOAD_DIR, out_name)

                cmd = [
                    "ffmpeg", "-y",
                    "-i", input_path,
                    "-ss", start, "-to", end,
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-b:a", "192k",
                    out_path
                ]
                subprocess.run(cmd, check=True)
                zipf.write(out_path, arcname=out_name)

        return FileResponse(zip_path, media_type="application/zip", filename="clips_bundle.zip")

    except Exception as e:
        print(f"❌ Multi-clip error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ============================================================
# 🎙️ WHISPER TRANSCRIBE
# ============================================================
@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(None), url: str = Form(None)):
    try:
        tmp_path = None
        os.makedirs("/tmp", exist_ok=True)

        if file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm", dir="/tmp") as tmp:
                tmp.write(await file.read())
                tmp_path = tmp.name
        elif url:
            response = requests.get(url, stream=True, timeout=60)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm", dir="/tmp") as tmp:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    tmp.write(chunk)
                tmp_path = tmp.name
        else:
            return JSONResponse({"error": "No file or URL provided."}, status_code=400)

        audio_mp3 = tmp_path.rsplit(".", 1)[0] + ".mp3"
        subprocess.run(["ffmpeg", "-y", "-i", tmp_path, "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", audio_mp3])

        with open(audio_mp3, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )

        os.remove(tmp_path)
        os.remove(audio_mp3)

        return {"text": transcript.strip()}

    except Exception as e:
        print(f"❌ Error during transcription: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
