import os, shutil, subprocess, asyncio, sys
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

# Optional: pip install openai yt-dlp
from openai import OpenAI
import yt_dlp

APP_NAME = "PTSEL Clipper"
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title=APP_NAME)

# --- CORS: frontend + local dev
ALLOWED_ORIGINS = [
    "https://ptsel-frontend.onrender.com",
    "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- OpenAI (optional for /transcribe)
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

# ---------- helpers ----------

def log(msg: str):
    print(msg, file=sys.stdout, flush=True)

def ffmpeg_run(cmd: list[str]) -> tuple[int, str]:
    """Run ffmpeg and return (code, stderr)."""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode, proc.stderr

def safe_trim(src: str, start: str, end: str, dst: str) -> tuple[bool, str]:
    """
    Try fast stream copy first (super fast).
    If it fails (non-keyframe, codec mismatch), fall back to re-encode.
    """
    # 1) fast path
    fast = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", start, "-to", end, "-i", src,
        "-c", "copy", "-y", dst
    ]
    code, err = ffmpeg_run(fast)
    if code == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
        return True, "copy"

    # 2) safe path (re-encode)
    slow = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", start, "-to", end, "-i", src,
        "-c:v", "libx264", "-preset", "veryfast",
        "-c:a", "aac", "-y", dst
    ]
    code2, err2 = ffmpeg_run(slow)
    if code2 == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
        return True, "reencode"

    return False, (err2 or err)

def autoclean(days: int = 3):
    now = datetime.now().timestamp()
    removed = 0
    for root, _, files in os.walk(UPLOAD_DIR):
        for f in files:
            p = os.path.join(root, f)
            try:
                if os.path.getmtime(p) < now - days*24*3600:
                    os.remove(p)
                    removed += 1
            except Exception as e:
                log(f"[cleanup] failed {p}: {e}")
    if removed:
        log(f"[cleanup] removed {removed} old files")

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(asyncio.to_thread(autoclean))
    log(f"[startup] {APP_NAME} ready. Upload dir: {UPLOAD_DIR}")

# ---------- tiny observability routes ----------

@app.get("/")
def home():
    return {"status": f"{APP_NAME} backend combined and running ✅"}

@app.get("/probe")
def probe():
    return {"ok": True, "uptime": datetime.utcnow().isoformat() + "Z"}

@app.post("/echo")
async def echo(request: Request):
    # Helps isolate proxy/CORS issues with a tiny body
    try:
        body = await request.body()
        return PlainTextResponse(f"len={len(body)} origin={request.headers.get('origin')}")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ---------- main features ----------

@app.post("/clip")
async def clip_video(
    request: Request,
    file: UploadFile = File(...),
    start: str = Form(...),
    end: str = Form(...),
):
    try:
        log(f"[clip] origin={request.headers.get('origin')} ua={request.headers.get('user-agent')}")
        src = os.path.join(UPLOAD_DIR, file.filename)
        with open(src, "wb") as buf:
            shutil.copyfileobj(file.file, buf)

        out_name = f"trimmed_{file.filename}"
        out_path = os.path.join(UPLOAD_DIR, out_name)

        ok, how = safe_trim(src, start.strip(), end.strip(), out_path)
        if not ok:
            return JSONResponse({"error": "FFmpeg failed to trim this file"}, status_code=500)

        log(f"[clip] done via {how}: {out_name} ({os.path.getsize(out_path)} bytes)")
        return FileResponse(out_path, filename=out_name)

    except Exception as e:
        log(f"[clip] error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/clip_link")
async def clip_youtube(
    request: Request,
    url: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
):
    try:
        log(f"[clip_link] origin={request.headers.get('origin')} url={url}")
        src = os.path.join(UPLOAD_DIR, "yt_source.mp4")
        out_path = os.path.join(UPLOAD_DIR, "yt_trimmed.mp4")

        ydl_opts = {"outtmpl": src, "format": "mp4", "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        ok, how = safe_trim(src, start.strip(), end.strip(), out_path)
        if not ok:
            return JSONResponse({"error": "FFmpeg failed to trim YouTube clip"}, status_code=500)

        log(f"[clip_link] done via {how}: yt_trimmed.mp4 ({os.path.getsize(out_path)} bytes)")
        return FileResponse(out_path, filename="yt_trimmed.mp4")

    except Exception as e:
        log(f"[clip_link] error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
):
    if not client:
        return JSONResponse({"error": "OPENAI_API_KEY not set"}, status_code=500)
    try:
        log(f"[transcribe] origin={request.headers.get('origin')}")
        src = os.path.join(UPLOAD_DIR, file.filename)
        with open(src, "wb") as buf:
            shutil.copyfileobj(file.file, buf)

        with open(src, "rb") as audio_file:
            resp = client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=audio_file
            )
        return {"text": resp.text}
    except Exception as e:
        log(f"[transcribe] error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ---------- uvicorn entry (Render binds $PORT) ----------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    log(f"[boot] binding 0.0.0.0:{port}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        timeout_keep_alive=180,   # a little headroom
        limit_concurrency=20,
    )
