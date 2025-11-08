import os
import io
import json
import shutil
import asyncio
import tempfile
import subprocess
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
from zipfile import ZipFile

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from openai import OpenAI
from supabase import create_client, Client
import requests

# ============================================================
# 🔧 App & Env
# ============================================================
app = FastAPI(title="ClipForge AI Backend (Stable Lite)", version="2.1.0")
client = OpenAI()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase: Optional[Client] = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

BASE_DIR = "/data"
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
PREVIEW_DIR = os.path.join(BASE_DIR, "previews")
TMP_DIR = "/tmp"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)
os.makedirs(PREVIEW_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)

# Serve generated files
app.mount("/media/exports", StaticFiles(directory=EXPORT_DIR), name="exports")
app.mount("/media/previews", StaticFiles(directory=PREVIEW_DIR), name="previews")

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

# Public base URL for absolute links (fallback to request.base_url)
PUBLIC_BASE = os.getenv("PUBLIC_BASE", "").rstrip("/")

# ============================================================
# 🧹 Cleanup
# ============================================================
def _cleanup_folder(folder: str, days_old: int = 3) -> int:
    now = datetime.now().timestamp()
    cutoff = now - days_old * 86400
    removed = 0
    for root, _, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    removed += 1
            except Exception:
                pass
    return removed

@app.on_event("startup")
async def _startup():
    async def _task():
        removed = 0
        removed += _cleanup_folder(UPLOAD_DIR)
        removed += _cleanup_folder(EXPORT_DIR)
        removed += _cleanup_folder(PREVIEW_DIR)
        if removed:
            print(f"🧹 Removed {removed} expired files")
    asyncio.create_task(_task())

# ============================================================
# ✅ Health
# ============================================================
@app.get("/")
def root():
    return {"ok": True, "service": "ClipForge AI Backend (Stable Lite) v2.1.0"}

# ============================================================
# 🧭 Utils
# ============================================================
def _run_ffmpeg(cmd: List[str], timeout: int = 1800) -> Tuple[int, str]:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return proc.returncode, proc.stderr

def _timestamp():
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")

def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in ("-", "_", "."))[:120]

def _compose_vf(scale_filter: str, drawtext: Optional[str]) -> List[str]:
    if drawtext:
        return ["-vf", f"{scale_filter},drawtext={drawtext}"]
    else:
        return ["-vf", scale_filter]

def _drawtext_expr(text: str) -> str:
    # bottom-right boxed watermark
    safe = text.replace("'", r"\'")
    return (
        f"text='{safe}':x=w-tw-20:y=h-th-20:"
        "fontcolor=white:fontsize=28:box=1:boxcolor=black@0.45:boxborderw=10"
    )

def _scale_filter(target_h: int) -> str:
    # keep aspect ratio; enforce even sizes for x264
    return f"scale=-2:{target_h}:flags=lanczos"

def _abs_url(request: Request, path: str) -> str:
    if PUBLIC_BASE:
        return f"{PUBLIC_BASE}{path}"
    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"

def _make_paths(original_name: str, start: str, end: str) -> Tuple[str, str]:
    base = _safe_name(os.path.splitext(original_name)[0] or "clip")
    stamp = _timestamp()
    preview_name = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_prev_{stamp}.mp4"
    final_name   = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_1080_{stamp}.mp4"
    return os.path.join(PREVIEW_DIR, preview_name), os.path.join(EXPORT_DIR, final_name)

def _download_to_tmp(url: str) -> str:
    """
    Download remote media to a temp file. Uses yt-dlp for known platforms,
    falls back to HTTP stream otherwise.
    """
    tmp_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    u = url.lower()
    if any(k in u for k in ["youtube", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com"]):
        p = subprocess.run(
            ["yt-dlp", "-f", "mp4", "-o", tmp_path, url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300
        )
        if p.returncode != 0 or not os.path.exists(tmp_path):
            raise RuntimeError("yt-dlp failed to fetch URL")
    else:
        r = requests.get(url, stream=True, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Failed to download: HTTP {r.status_code}")
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
    return tmp_path

# ============================================================
# 🔊 Transcription (Whisper) + Supabase save
# ============================================================
@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(None),
    url: str = Form(None),
    user_email: str = Form("guest@clipforge.app")
):
    """
    Always returns: { "text": "<transcript>" } on 200.
    On failure: { "error": "<reason>" } with 4xx/5xx.
    URL path: tries direct MP3 extract; falls back to MP4→MP3.
    Supabase: never blocks success; auto-fallback to common columns.
    """
    base_marker = f"audio_{nowstamp()}"
    candidate_base = os.path.join(TMP_DIR, base_marker)

    def find_mp3s() -> List[str]:
        return glob.glob(os.path.join(TMP_DIR, f"{base_marker}*.mp3"))

    tmp_paths = []  # collect temp files for cleanup
    try:
        # ------------- Resolve to MP3 -------------
        mp3_path = None

        if url:
            # Try direct audio extract to MP3
            code, err = run([
                "yt-dlp", "--no-playlist",
                "-x", "--audio-format", "mp3", "--audio-quality", "192K",
                "-o", candidate_base + ".%(ext)s",
                "--force-overwrites", url
            ], timeout=900)

            cands = find_mp3s()
            if code == 0 and cands:
                mp3_path = sorted(cands, key=lambda p: os.path.getmtime(p))[-1]
                tmp_paths.append(mp3_path)
            else:
                # Fallback: fetch MP4 then convert
                src = candidate_base + ".mp4"
                code2, err2 = run([
                    "yt-dlp","--no-playlist","-f","mp4","-o",src,"--force-overwrites", url
                ], timeout=900)
                if code2 != 0 or not os.path.exists(src):
                    return JSONResponse({"error": f"Failed to fetch URL.\n{(err or err2)[:1200]}"},
                                        status_code=400)
                tmp_paths.append(src)
                mp3_path = candidate_base + ".mp3"
                code3, err3 = run([
                    "ffmpeg","-y","-i",src,"-vn","-acodec","libmp3lame","-b:a","192k", mp3_path
                ], timeout=900)
                if code3 != 0 or not os.path.exists(mp3_path):
                    return JSONResponse({"error": f"FFmpeg convert failed.\n{(err3 or '')[:1200]}"},
                                        status_code=500)
                tmp_paths.append(mp3_path)

        elif file is not None:
            src_name = safe(file.filename or "audio.webm")
            src = os.path.join(TMP_DIR, f"upl_{nowstamp()}_{src_name}")
            with open(src, "wb") as f:
                shutil.copyfileobj(file.file, f)
            tmp_paths.append(src)

            mp3_path = src.rsplit(".",1)[0] + ".mp3"
            code, err = run([
                "ffmpeg","-y","-i",src,"-vn","-acodec","libmp3lame","-b:a","192k", mp3_path
            ], timeout=900)
            if code != 0 or not os.path.exists(mp3_path):
                return JSONResponse({"error": f"FFmpeg audio convert failed.\n{(err or '')[:1200]}"},
                                    status_code=500)
            tmp_paths.append(mp3_path)
        else:
            return JSONResponse({"error":"No file or URL provided."}, status_code=400)

        # ------------- Whisper -------------
        with open(mp3_path, "rb") as a:
            tr = client.audio.transcriptions.create(model="whisper-1", file=a, response_format="text")
        text = tr.strip() if isinstance(tr, str) else str(tr)
        if not text:
            text = "(no text)"

        # ------------- Supabase (non-blocking) -------------
        if supabase:
            try:
                # try supplied cols
                payload = {
                    SUPABASE_EMAIL_COLUMN: user_email,
                    SUPABASE_TEXT_COLUMN: text,
                    "created_at": datetime.utcnow().isoformat()
                }
                resp = supabase.table(SUPABASE_TABLE).insert(payload).execute()
            except Exception as e1:
                # common fallback columns if schema differs
                try:
                    alt_payloads = [
                        {"user_email": user_email, "content": text, "created_at": datetime.utcnow().isoformat()},
                        {"email": user_email, "text": text, "created_at": datetime.utcnow().isoformat()},
                        {"data": {"email": user_email, "text": text}, "created_at": datetime.utcnow().isoformat()},
                    ]
                    ok = False
                    for alt in alt_payloads:
                        try:
                            supabase.table(SUPABASE_TABLE).insert(alt).execute()
                            ok = True
                            break
                        except Exception:
                            continue
                    if not ok:
                        print("⚠️ Supabase insert still failing. Last error:", e1)
                except Exception as e2:
                    print("⚠️ Supabase fallback failed:", e2)

        # ------------- Stable JSON for UI -------------
        return JSONResponse({"text": text}, status_code=200)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        # Cleanup temp
        for p in tmp_paths:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except:
                pass
