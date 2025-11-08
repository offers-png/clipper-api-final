# app.py
import os, shutil, asyncio, tempfile, subprocess, requests, json
from datetime import datetime, timedelta
from zipfile import ZipFile
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

app = FastAPI()
client = OpenAI()  # requires OPENAI_API_KEY

origins = [
    "https://ptsel-frontend.onrender.com",
    "https://clipper-frontend.onrender.com",
    "https://clipper-api-final-1.onrender.com",
    "http://localhost:5173",
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

def auto_cleanup():
    now = datetime.now()
    cutoff = (now - timedelta(days=3)).timestamp()
    removed = 0
    for root, _, files in os.walk(UPLOAD_DIR):
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path); removed += 1
            except Exception:
                pass
    if removed:
        print(f"🧹 Removed {removed} old files from {UPLOAD_DIR}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(asyncio.to_thread(auto_cleanup))

@app.get("/api/health")
def health():
    return {"ok": True, "message": "Backend is alive and ready"}

@app.get("/")
def root():
    return {"status": "✅ PTSEL Clipper + Whisper API is live and ready!"}

# -------- SINGLE CLIP --------
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

        safe_name = os.path.basename(file.filename)
        input_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        base, _ = os.path.splitext(safe_name)
        output_path = os.path.join(UPLOAD_DIR, f"{base}_trimmed.mp4")

        cmd = [
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-to", end, "-i", input_path,
            "-c:v","libx264","-preset","ultrafast",
            "-c:a","aac","-b:a","192k","-y", output_path
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1800)
        if p.returncode != 0 or not os.path.exists(output_path):
            print("❌ FFmpeg stderr:", p.stderr)
            return JSONResponse({"error": f"FFmpeg failed: {p.stderr}"}, status_code=500)

        return FileResponse(output_path, filename=f"{base}_trimmed.mp4", media_type="video/mp4")

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "⏱️ FFmpeg timed out while processing video."}, status_code=504)
    except Exception as e:
        print(f"❌ /clip error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# -------- MULTI CLIP (ZIP) --------
@app.post("/clip_multi")
async def clip_multi(file: UploadFile = File(...), sections: str = Form(...)):
    try:
        data = json.loads(sections)
        if not isinstance(data, list) or len(data) == 0:
            return JSONResponse({"error": "sections must be a non-empty JSON array"}, status_code=400)
        if len(data) > 5:
            return JSONResponse({"error": "Maximum 5 sections allowed"}, status_code=400)

        safe_name = os.path.basename(file.filename)
        input_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        zip_path = os.path.join(UPLOAD_DIR, "clips_bundle.zip")
        try:
            if os.path.exists(zip_path): os.remove(zip_path)
        except Exception:
            pass

        with ZipFile(zip_path, "w") as zipf:
            for idx, sec in enumerate(data, start=1):
                start = (sec.get("start") or "").strip()
                end   = (sec.get("end")   or "").strip()
                if not start or not end:
                    return JSONResponse({"error": f"Section {idx} missing start/end"}, status_code=400)

                out_name = f"clip_{idx}_{safe_name}.mp4"
                out_path = os.path.join(UPLOAD_DIR, out_name)
                cmd = [
                    "ffmpeg","-hide_banner","-loglevel","error",
                    "-ss", start,"-to", end,"-i", input_path,
                    "-c:v","libx264","-preset","ultrafast",
                    "-c:a","aac","-b:a","192k","-y", out_path
                ]
                p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1200)
                if p.returncode != 0 or not os.path.exists(out_path):
                    print(f"❌ FFmpeg section {idx} stderr:", p.stderr)
                    return JSONResponse({"error": f"FFmpeg failed on section {idx}"}, status_code=500)

                zipf.write(out_path, arcname=out_name)

        return FileResponse(zip_path, media_type="application/zip", filename="clips_bundle.zip")

    except Exception as e:
        print(f"❌ /clip_multi error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# -------- TRANSCRIBE (upload or URL) --------
@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(None), url: str = Form(None)):
    tmp_path, audio_mp3 = None, None
    try:
        os.makedirs("/tmp", exist_ok=True)

        if file:
            suffix = os.path.splitext(file.filename)[1] or ".webm"
            tmp_path = os.path.join("/tmp", f"upl_{datetime.now().timestamp()}{suffix}")
            with open(tmp_path, "wb") as f: f.write(await file.read())

        elif url:
            u = url.lower()
            social = any(k in u for k in ["tiktok.com","youtube.com","youtu.be","instagram.com","facebook.com","x.com","twitter.com"])
            if social:
                tmp_download = os.path.join("/tmp", f"remote_{datetime.now().timestamp()}.mp4")
                p = subprocess.run(
                    ["yt-dlp","-f","mp4","-o", tmp_download, url],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=420
                )
                if p.returncode != 0:
                    print("❌ yt-dlp stderr:", p.stderr)
                    return JSONResponse({"error":"yt-dlp failed to fetch the URL"}, status_code=400)
                if not os.path.exists(tmp_download) or os.path.getsize(tmp_download)==0:
                    return JSONResponse({"error":"Downloaded file is empty"}, status_code=400)
                tmp_path = tmp_download
            else:
                resp = requests.get(url, stream=True, timeout=60)
                if resp.status_code != 200:
                    return JSONResponse({"error": f"Failed to download file: HTTP {resp.status_code}"}, status_code=400)
                ext = ".mp3" if ".mp3" in u else ".mp4" if ".mp4" in u else ".m4a" if ".m4a" in u else ".wav" if ".wav" in u else ".webm"
                tmp_download = os.path.join("/tmp", f"remote_{datetime.now().timestamp()}{ext}")
                with open(tmp_download, "wb") as f:
                    for chunk in resp.iter_content(8192): f.write(chunk)
                if not os.path.exists(tmp_download) or os.path.getsize(tmp_download)==0:
                    return JSONResponse({"error":"Downloaded file is empty or missing."}, status_code=400)
                tmp_path = tmp_download
        else:
            return JSONResponse({"error":"No file or URL provided."}, status_code=400)

        audio_mp3 = tmp_path.rsplit(".",1)[0] + ".mp3"
        p = subprocess.run(
            ["ffmpeg","-y","-i", tmp_path, "-vn","-acodec","libmp3lame","-b:a","192k", audio_mp3],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if p.returncode != 0 or not os.path.exists(audio_mp3):
            print("❌ FFmpeg to mp3 stderr:", p.stderr)
            return JSONResponse({"error":"FFmpeg failed to create audio file"}, status_code=500)

        with open(audio_mp3, "rb") as a:
            tr = client.audio.transcriptions.create(model="whisper-1", file=a, response_format="text")

        text_output = tr.strip() if isinstance(tr, str) else str(tr)
        if not text_output: text_output = "(no text found — maybe silent or unreadable audio)"
        return JSONResponse({"text": text_output})

    except Exception as e:
        print(f"❌ /transcribe error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        for pth in (tmp_path, audio_mp3):
            try:
                if pth and os.path.exists(pth): os.remove(pth)
            except Exception: pass
