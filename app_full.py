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

# ============================================================
# 🔧 FastAPI & OpenAI Setup
# ============================================================
app = FastAPI()
client = OpenAI()

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

UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ============================================================
# 🧹 Auto-cleanup
# ============================================================
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                try:
                    os.remove(path)
                except Exception:
                    pass


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(asyncio.to_thread(auto_cleanup))


# ============================================================
# 🎬 TRANSCRIBE ENDPOINT
# ============================================================
@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(None),
    url: str = Form(None),
    mode: str = Form("transcribe")
):
    tmp_path = None

    try:
        # ====================================================
        # 📁 Option 1: Direct File Upload
        # ====================================================
        if file:
            tmp_path = os.path.join(UPLOAD_DIR, f"{datetime.now().timestamp()}_{file.filename}")
            with open(tmp_path, "wb") as f:
                f.write(await file.read())

        # ====================================================
        # 🌐 Option 2: Remote URL (TikTok / YouTube / etc.)
        # ====================================================
        elif url:
            try:
                # ✅ Use yt-dlp to download from TikTok, YouTube, Instagram, etc.
                tmp_download = os.path.join("/tmp", f"remote_{datetime.now().timestamp()}.mp4")
                subprocess.run(
                    [
                        "yt-dlp",
                        "-f", "mp4",
                        "-o", tmp_download,
                        url
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=120
                )

                # ✅ Check that file exists
                if not os.path.exists(tmp_download) or os.path.getsize(tmp_download) == 0:
                    return JSONResponse({"error": "Failed to download video using yt-dlp"}, status_code=400)

                tmp_path = tmp_download
                print(f"✅ Downloaded successfully via yt-dlp: {tmp_path}")

            except Exception as e:
                print("❌ yt-dlp error:", e)
                return JSONResponse({"error": f"yt-dlp failed: {e}"}, status_code=500)

        else:
            return JSONResponse({"error": "No file or URL provided"}, status_code=400)

        # ====================================================
        # 🎧 Extract Audio from Video
        # ====================================================
        audio_path = tmp_path.rsplit(".", 1)[0] + ".mp3"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_path, "-vn", "-acodec", "libmp3lame", audio_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60
            )
        except Exception as e:
            return JSONResponse({"error": f"FFmpeg failed to create audio file: {e}"}, status_code=500)

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            return JSONResponse({"error": "FFmpeg failed to create audio file"}, status_code=400)

        # ====================================================
        # 🧠 Transcribe using OpenAI Whisper
        # ====================================================
        with open(audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=audio_file
            )

        transcript_text = transcript.text if hasattr(transcript, "text") else str(transcript)

        # ====================================================
        # 📤 Return Transcript
        # ====================================================
        return JSONResponse({
            "transcript": transcript_text,
            "file_used": os.path.basename(tmp_path),
            "audio_file": os.path.basename(audio_path)
        })

    except Exception as e:
        return JSONResponse({"error": f"Server error: {e}"}, status_code=500)

    finally:
        # ✅ Cleanup temporary files
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        if "audio_path" in locals() and os.path.exists(audio_path):
            os.remove(audio_path)


# ============================================================
# 🧩 ROOT TEST
# ============================================================
@app.get("/")
def root():
    return {"message": "PTSEL Multi-Clip API is running successfully!"}
