import os
import shutil
import asyncio
import subprocess
import tempfile
import json
import requests
from datetime import datetime, timedelta
from zipfile import ZipFile
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from supabase import create_client, Client

# ============================================================
# 🔧 FastAPI & Environment Setup
# ============================================================
app = FastAPI(title="PTSEL Clipper Studio API", version="1.0.0")
client = OpenAI()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://clipper-frontend.onrender.com",
        "https://ptsel-frontend.onrender.com",
        "https://clipper-api-final-1.onrender.com",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 🧹 Auto-cleanup (3 days old)
# ============================================================
def auto_cleanup():
    now = datetime.now()
    cutoff = (now - timedelta(days=3)).timestamp()
    removed = 0
    for root, _, files in os.walk(UPLOAD_DIR):
        for name in files:
            path = os.path.join(root, name)
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
    if removed:
        print(f"🧹 Removed {removed} expired files")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(asyncio.to_thread(auto_cleanup))

# ============================================================
# ✅ Health
# ============================================================
@app.get("/")
def root():
    return {"ok": True, "msg": "✅ PTSEL Clipper API is alive!"}

# ============================================================
# 🎬 SINGLE CLIP + optional watermark
# ============================================================
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
import os, shutil, subprocess, json
from zipfile import ZipFile

@app.post("/clip")
async def clip_video(
    file: UploadFile = File(...),
    start: str = Form(...),
    end: str = Form(...),
    watermark: str = Form(""),
    fast: str = Form("1")   # "1" = try stream copy for speed
):
    try:
        start, end = start.strip(), end.strip()
        if not start or not end:
            return JSONResponse({"error":"Start and end required."}, 400)

        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        base, _ = os.path.splitext(file.filename)
        output_path = os.path.join(UPLOAD_DIR, f"{base}_{start.replace(':','-')}-{end.replace(':','-')}.mp4")

        vf = []
        # Watermark (bottom-right)
        if watermark:
            draw = (
              "drawtext=text='{t}':x=w-tw-20:y=h-th-20:"
              "fontcolor=white:fontsize=28:"
              "box=1:boxcolor=black@0.45:boxborderw=10"
            ).format(t=watermark.replace("'", r"\'"))
            vf = ["-vf", draw]

        # Fast path: stream copy video (if segment is simple & codec compatible)
        if fast == "1" and not vf:
            cmd = [
              "ffmpeg","-hide_banner","-loglevel","error",
              "-ss", start, "-to", end, "-i", input_path,
              "-c:v","copy","-c:a","aac","-b:a","192k","-y", output_path
            ]
        else:
            cmd = [
              "ffmpeg","-hide_banner","-loglevel","error",
              "-ss", start, "-to", end, "-i", input_path,
              "-c:v","libx264","-preset","veryfast","-c:a","aac","-b:a","192k","-y"
            ] + vf + [output_path]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1800)
        if result.returncode != 0 or not os.path.exists(output_path):
            return JSONResponse({"error":f"FFmpeg failed: {result.stderr}"}, 500)

        return FileResponse(output_path, filename=os.path.basename(output_path), media_type="video/mp4")
    except subprocess.TimeoutExpired:
        return JSONResponse({"error":"FFmpeg timed out."}, 504)
    except Exception as e:
        return JSONResponse({"error":str(e)}, 500)

@app.post("/clip_multi")
async def clip_multi(
    file: UploadFile = File(...),
    sections: str = Form(...),
    watermark: str = Form(""),
    fast: str = Form("1")
):
    try:
        data = json.loads(sections)
        if not isinstance(data, list) or not data:
            return JSONResponse({"error":"sections must be a JSON array"}, 400)

        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        zip_path = os.path.join(UPLOAD_DIR, "clips_bundle.zip")
        if os.path.exists(zip_path): os.remove(zip_path)

        with ZipFile(zip_path, "w") as zipf:
            for idx, sec in enumerate(data, start=1):
                start = str(sec.get("start","")).strip()
                end   = str(sec.get("end","")).strip()
                if not start or not end:
                    return JSONResponse({"error":f"Missing start/end in section {idx}"}, 400)

                out_name = f"clip_{idx}.mp4"
                out_path = os.path.join(UPLOAD_DIR, out_name)

                vf = []
                if watermark:
                    draw = (
                      "drawtext=text='{t}':x=w-tw-20:y=h-th-20:"
                      "fontcolor=white:fontsize=28:box=1:boxcolor=black@0.45:boxborderw=10"
                    ).format(t=watermark.replace("'", r"\'"))
                    vf = ["-vf", draw]

                if fast == "1" and not vf:
                    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-ss",start,"-to",end,"-i",input_path,
                           "-c:v","copy","-c:a","aac","-b:a","192k","-y", out_path]
                else:
                    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-ss",start,"-to",end,"-i",input_path,
                           "-c:v","libx264","-preset","veryfast","-c:a","aac","-b:a","192k","-y"] + vf + [out_path]

                r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if r.returncode != 0 or not os.path.exists(out_path):
                    return JSONResponse({"error":f"FFmpeg failed on section {idx}: {r.stderr}"}, 500)

                zipf.write(out_path, arcname=out_name)

        return FileResponse(zip_path, media_type="application/zip", filename="clips_bundle.zip")
    except Exception as e:
        return JSONResponse({"error":str(e)}, 500)


# ============================================================
# 🎙️ TRANSCRIBE (Whisper + Supabase Save)
# ============================================================
@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(None),
    url: str = Form(None),
    user_email: str = Form("guest@clipper.com")
):
    tmp_path = None
    audio_mp3 = None
    try:
        os.makedirs("/tmp", exist_ok=True)
        # --- A) File Upload ---
        if file:
            suffix = os.path.splitext(file.filename)[1] or ".mp3"
            tmp_path = os.path.join("/tmp", f"upl_{datetime.now().timestamp()}{suffix}")
            with open(tmp_path, "wb") as f:
                f.write(await file.read())
        # --- B) URL ---
        elif url:
            tmp_path = os.path.join("/tmp", f"remote_{datetime.now().timestamp()}.mp4")
            subprocess.run(["yt-dlp", "-f", "mp4", "-o", tmp_path, url], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
            return JSONResponse({"error": "No file or URL provided."}, status_code=400)

        # --- Convert to MP3 ---
        audio_mp3 = tmp_path.rsplit(".", 1)[0] + ".mp3"
        subprocess.run(["ffmpeg", "-y", "-i", tmp_path, "-vn", "-acodec", "libmp3lame", "-b:a", "192k", audio_mp3])

        # --- Transcribe ---
        with open(audio_mp3, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file, response_format="text")

        text_output = transcript.strip() if isinstance(transcript, str) else str(transcript)
        if not text_output:
            text_output = "(no text found)"

        supabase.table("transcriptions").insert({
            "user_email": user_email,
            "text": text_output,
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        return JSONResponse({"text": text_output})
    except Exception as e:
        print(f"❌ /transcribe error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        for p in [tmp_path, audio_mp3]:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
