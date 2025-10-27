import os
import shutil
import asyncio
import subprocess
import requests
import json
from datetime import datetime, timedelta
from zipfile import ZipFile

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

# =========================
# Setup
# =========================
app = FastAPI()
client = OpenAI()  # Needs OPENAI_API_KEY in env (Render)

ALLOWED_ORIGINS = [
    "https://ptsel-frontend.onrender.com",
    "https://clipper-frontend.onrender.com",
    "https://clipper-api-final-1.onrender.com",
    "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "/data/uploads"
TMP_DIR = "/tmp"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)

# =========================
# Housekeeping
# =========================
def auto_cleanup(days=3):
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    removed = 0
    for root, _, files in os.walk(UPLOAD_DIR):
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except Exception:
                pass
    if removed:
        print(f"🧹 Removed {removed} old files")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(asyncio.to_thread(auto_cleanup))

# =========================
# Health
# =========================
@app.get("/api/health")
def health():
    return {"ok": True, "message": "Backend is alive and ready"}

@app.get("/")
def root():
    return {"status": "✅ PTSEL Clipper + Whisper API is live and ready!"}

# =========================
# Clip - Single
# =========================
@app.post("/clip")
async def clip_video(
    file: UploadFile = File(...),
    start: str = Form(...),
    end: str = Form(...)
):
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

        return FileResponse(output_path, filename=os.path.basename(output_path), media_type="video/mp4")

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "⏱️ FFmpeg timed out."}, status_code=504)
    except Exception as e:
        print(f"❌ /clip error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# =========================
# Clip - Multi (ZIP)
# sections: [{start, end}, ...]
# =========================
@app.post("/clip_multi")
async def clip_multi(file: UploadFile = File(...), sections: str = Form(...)):
    try:
        data = json.loads(sections)
        if not isinstance(data, list) or len(data) == 0:
            return JSONResponse({"error": "sections must be a JSON array"}, status_code=400)

        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        zip_path = os.path.join(UPLOAD_DIR, "clips_bundle.zip")
        if os.path.exists(zip_path):
            os.remove(zip_path)

        with ZipFile(zip_path, "w") as zipf:
            for idx, sec in enumerate(data, start=1):
                start = str(sec.get("start", "")).strip()
                end = str(sec.get("end", "")).strip()
                if not start or not end:
                    return JSONResponse({"error": f"Missing start/end in section {idx}"}, status_code=400)

                out_name = f"clip_{idx}_{os.path.basename(file.filename)}.mp4"
                out_path = os.path.join(UPLOAD_DIR, out_name)

                cmd = [
                    "ffmpeg", "-y",
                    "-ss", start, "-to", end,
                    "-i", input_path,
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-b:a", "192k",
                    out_path
                ]
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if result.returncode != 0 or not os.path.exists(out_path):
                    print(f"❌ FFmpeg section {idx} error:", result.stderr)
                    return JSONResponse({"error": f"FFmpeg failed on section {idx}"}, status_code=500)

                zipf.write(out_path, arcname=out_name)

        return FileResponse(zip_path, media_type="application/zip", filename="clips_bundle.zip")

    except Exception as e:
        print(f"❌ /clip_multi error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# =========================
# Transcribe (upload or URL)
# Returns: { text, segments: [{start,end,text}], duration }
# =========================
@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(None),
    url: str = Form(None)
):
    tmp_path = None
    audio_mp3 = None

    try:
        # A) Upload
        if file:
            suffix = os.path.splitext(file.filename)[1] or ".webm"
            tmp_path = os.path.join(TMP_DIR, f"upl_{datetime.now().timestamp()}{suffix}")
            with open(tmp_path, "wb") as f:
                f.write(await file.read())

        # B) URL
        elif url:
            url_l = url.lower()
            if any(k in url_l for k in ["tiktok.com", "youtube", "youtu.be", "instagram.com", "facebook.com", "x.com"]):
                tmp_download = os.path.join(TMP_DIR, f"remote_{datetime.now().timestamp()}.mp4")
                proc = subprocess.run(
                    ["yt-dlp", "-f", "mp4", "-o", tmp_download, url],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180
                )
                if proc.returncode != 0:
                    print("❌ yt-dlp stderr:", proc.stderr)
                    return JSONResponse({"error": "yt-dlp failed to fetch URL"}, status_code=400)
                tmp_path = tmp_download
            else:
                resp = requests.get(url, stream=True, timeout=60)
                if resp.status_code != 200:
                    return JSONResponse({"error": f"Failed to download file: HTTP {resp.status_code}"}, status_code=400)

                ext = ".mp3" if ".mp3" in url_l else ".mp4" if ".mp4" in url_l else ".webm"
                tmp_download = os.path.join(TMP_DIR, f"remote_{datetime.now().timestamp()}{ext}")
                with open(tmp_download, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                tmp_path = tmp_download
        else:
            return JSONResponse({"error": "No file or URL provided."}, status_code=400)

        # Convert to MP3 (if needed)
        if tmp_path.lower().endswith(".mp3"):
            audio_mp3 = tmp_path
        else:
            audio_mp3 = tmp_path.rsplit(".", 1)[0] + ".mp3"
            proc_aud = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_path, "-vn", "-acodec", "libmp3lame", "-b:a", "192k", audio_mp3],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if proc_aud.returncode != 0 or not os.path.exists(audio_mp3):
                print("❌ FFmpeg audio error:", proc_aud.stderr)
                return JSONResponse({"error": "FFmpeg failed to create audio file"}, status_code=500)

        # Whisper (verbose for timestamps)
        with open(audio_mp3, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json"  # includes segments with start/end
            )

        # Build response
        # result.segments: list of {start, end, text, id, ...}
        segments = []
        total_text_parts = []
        duration = 0.0

        if isinstance(result, dict):
            # Safety if SDK representation changes
            raw_segments = result.get("segments", []) or []
            for seg in raw_segments:
                s = float(seg.get("start", 0))
                e = float(seg.get("end", 0))
                t = (seg.get("text") or "").strip()
                segments.append({"start": s, "end": e, "text": t})
                total_text_parts.append(t)
                duration = max(duration, e)
            full_text = " ".join(total_text_parts).strip()
        else:
            # Current SDK object style
            raw_segments = getattr(result, "segments", []) or []
            for seg in raw_segments:
                s = float(getattr(seg, "start", 0) or 0)
                e = float(getattr(seg, "end", 0) or 0)
                t = (getattr(seg, "text", "") or "").strip()
                segments.append({"start": s, "end": e, "text": t})
                total_text_parts.append(t)
                duration = max(duration, e)
            full_text = " ".join(total_text_parts).strip()

        if not full_text:
            full_text = "(no text found — maybe silent or unreadable audio)"

        return JSONResponse({
            "text": full_text,
            "segments": segments,
            "duration": duration
        })

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
