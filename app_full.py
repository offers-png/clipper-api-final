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
    tmp_path = None
    audio_mp3 = None
    try:
        if file:
            suffix = os.path.splitext(file.filename)[1] or ".webm"
            tmp_path = os.path.join(TMP_DIR, f"upl_{_timestamp()}{suffix}")
            with open(tmp_path, "wb") as f:
                f.write(await file.read())
        elif url:
            tmp_path = _download_to_tmp(url)
        else:
            return JSONResponse({"error": "No file or URL provided."}, 400)

        # Convert to MP3 for Whisper
        audio_mp3 = tmp_path.rsplit(".", 1)[0] + ".mp3"
        code, err = _run_ffmpeg(["ffmpeg", "-y", "-i", tmp_path, "-vn", "-acodec", "libmp3lame", "-b:a", "192k", audio_mp3])
        if code != 0 or not os.path.exists(audio_mp3):
            return JSONResponse({"error": f"FFmpeg audio convert failed: {err}"}, 500)

        # Whisper
        with open(audio_mp3, "rb") as audio_file:
            tr = client.audio.transcriptions.create(model="whisper-1", file=audio_file, response_format="text")

        text_output = tr.strip() if isinstance(tr, str) else str(tr)
        if not text_output:
            text_output = "(no text found)"

        # Save to Supabase if configured
        if supabase:
            try:
                supabase.table("transcriptions").insert({
                    "user_email": user_email,
                    "text": text_output,
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                print("⚠️ Supabase insert error:", e)

        return JSONResponse({"text": text_output})
    except Exception as e:
        print("❌ /transcribe error:", e)
        return JSONResponse({"error": str(e)}, 500)
    finally:
        for p in (tmp_path, audio_mp3):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

# ============================================================
# 🤖 AI Helper (titles, hooks, moments)
# ============================================================
SYSTEM_PROMPT = (
    "You are ClipForge AI, an editing copilot. Be concise and practical. "
    "When asked to find moments, suggest 10–45s ranges using HH:MM:SS."
)

@app.post("/ai_chat")
async def ai_chat(
    user_message: str = Form(...),
    transcript: str = Form(""),
    history: str = Form("[]")
):
    try:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        if transcript:
            msgs.append({"role": "system", "content": f"Transcript:\n{transcript[:12000]}"})
        try:
            prev = json.loads(history) if history else []
            if isinstance(prev, list):
                msgs.extend(prev)
        except Exception:
            pass
        msgs.append({"role": "user", "content": user_message})

        resp = client.chat.completions.create(model="gpt-4o-mini", temperature=0.3, messages=msgs)
        out = resp.choices[0].message.content.strip()
        return JSONResponse({"reply": out})
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

@app.post("/auto_clip")
async def auto_clip(transcript: str = Form(...), max_clips: int = Form(3)):
    try:
        prompt = (
            "From this transcript, pick up to {k} high-impact short moments (10–45s). "
            "Return strict JSON key 'clips' = list of objects with start,end,summary.\n\nTranscript:\n{t}"
        ).format(k=max_clips, t=transcript[:12000])
        resp = client.chat.completions.create(model="gpt-4o-mini", temperature=0.2, messages=[{"role": "user", "content": prompt}])
        raw = resp.choices[0].message.content
        try:
            data = json.loads(raw)
        except Exception:
            s, e = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[s:e+1]) if s != -1 and e != -1 else {"clips": []}
        clips = data.get("clips", [])
        out = []
        for c in clips[:max_clips]:
            out.append({
                "start": str(c.get("start","00:00:00")).strip(),
                "end": str(c.get("end","00:00:10")).strip(),
                "summary": str(c.get("summary","")).strip()[:140]
            })
        return JSONResponse({"clips": out})
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

# ============================================================
# 🎬 Clip pipeline (Preview 480p + Optional Export 1080p)
#   — supports FILE or URL —
#   — returns ABSOLUTE URLs —
# ============================================================
async def _clip_once(
    source_path: str,
    start: str,
    end: str,
    wm_text: Optional[str],
    want_preview_480: bool = True,
    want_final_1080: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    preview_out, final_out = _make_paths(os.path.basename(source_path), start, end)
    drawtext = _drawtext_expr(wm_text) if wm_text else None

    # Preview (fast, small)
    if want_preview_480:
        cmd_prev = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", start, "-to", end, "-i", source_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-b:a", "128k",
        ] + _compose_vf(_scale_filter(480), drawtext) + ["-movflags", "+faststart", "-y", preview_out]
        code, err = _run_ffmpeg(cmd_prev, timeout=1200)
        if code != 0 or not os.path.exists(preview_out):
            raise RuntimeError(f"Preview failed: {err}")

    # Final (quality)
    if want_final_1080:
        cmd_final = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", start, "-to", end, "-i", source_path,
            "-c:v", "libx264", "-preset", "faster", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
        ] + _compose_vf(_scale_filter(1080), drawtext) + ["-movflags", "+faststart", "-y", final_out]
        code2, err2 = _run_ffmpeg(cmd_final, timeout=1800)
        if code2 != 0 or not os.path.exists(final_out):
            raise RuntimeError(f"Final export failed: {err2}")

    return (
        f"/media/previews/{os.path.basename(preview_out)}" if want_preview_480 else None,
        f"/media/exports/{os.path.basename(final_out)}" if want_final_1080 else None,
    )

@app.post("/clip")
async def clip_video(
    request: Request,
    file: UploadFile = File(None),          # now optional
    url: str = Form(None),                  # NEW: URL support
    start: str = Form(...),
    end: str = Form(...),
    watermark: str = Form("0"),
    wm_text: str = Form("@ClipForge"),
    preview_480: str = Form("1"),
    final_1080: str = Form("0"),
):
    tmp = None
    try:
        start = start.strip()
        end = end.strip()
        if not start or not end:
            return JSONResponse({"error": "Start and end required."}, 400)

        # Resolve source to a local path
        if file is not None:
            src_path = os.path.join(UPLOAD_DIR, _safe_name(file.filename or f"upload_{_timestamp()}.mp4"))
            with open(src_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
        elif url:
            tmp = _download_to_tmp(url)
            # keep a safe copy name for consistent output names
            safe_name = _safe_name(os.path.basename(url) or f"remote_{_timestamp()}.mp4")
            src_path = os.path.join(UPLOAD_DIR, safe_name)
            shutil.copy(tmp, src_path)
        else:
            return JSONResponse({"error": "Provide a file or a url."}, 400)

        prev_rel, final_rel = await _clip_once(
            source_path=src_path,
            start=start,
            end=end,
            wm_text=wm_text if watermark == "1" else None,
            want_preview_480=(preview_480 == "1"),
            want_final_1080=(final_1080 == "1"),
        )

        # Return absolute URLs for the frontend
        return JSONResponse({
            "ok": True,
            "preview_url": _abs_url(request, prev_rel) if prev_rel else None,
            "final_url": _abs_url(request, final_rel) if final_rel else None,
            "start": start,
            "end": end,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)
    finally:
        try:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

@app.post("/clip_multi")
async def clip_multi(
    request: Request,
    file: UploadFile = File(None),           # now optional
    url: str = Form(None),                   # NEW
    sections: str = Form(...),               # [{"start":"..","end":".."}, ...]
    watermark: str = Form("0"),
    wm_text: str = Form("@ClipForge"),
    preview_480: str = Form("1"),
    final_1080: str = Form("0"),
):
    tmp = None
    try:
        # Resolve source
        if file is not None:
            src_path = os.path.join(UPLOAD_DIR, _safe_name(file.filename or f"upload_{_timestamp()}.mp4"))
            with open(src_path, "wb") as f:
                f.write(await file.read())
        elif url:
            tmp = _download_to_tmp(url)
            safe_name = _safe_name(os.path.basename(url) or f"remote_{_timestamp()}.mp4")
            src_path = os.path.join(UPLOAD_DIR, safe_name)
            shutil.copy(tmp, src_path)
        else:
            return JSONResponse({"error": "Provide a file or a url."}, 400)

        try:
            segs = json.loads(sections)
        except Exception:
            return JSONResponse({"error": "sections must be valid JSON list"}, 400)
        if not isinstance(segs, list) or not segs:
            return JSONResponse({"error": "sections must be a non-empty list"}, 400)

        wm = wm_text if watermark == "1" else None
        want_prev = (preview_480 == "1")
        want_final = (final_1080 == "1")

        sem = asyncio.Semaphore(3)
        results = []

        async def _worker(s, e):
            async with sem:
                pr, fn = await _clip_once(src_path, s, e, wm, want_prev, want_final)
                return {
                    "preview_url": _abs_url(request, pr) if pr else None,
                    "final_url": _abs_url(request, fn) if fn else None,
                    "start": s,
                    "end": e,
                }

        tasks = [ _worker(str(s.get("start","")).strip(), str(s.get("end","")).strip()) for s in segs ]
        results = await asyncio.gather(*tasks)

        zip_url = None
        if want_final:
            zip_name = f"clips_{_timestamp()}.zip"
            zip_path = os.path.join(EXPORT_DIR, zip_name)
            with ZipFile(zip_path, "w") as z:
                for r in results:
                    if r.get("final_url"):
                        final_file = os.path.join(EXPORT_DIR, os.path.basename(r["final_url"]))
                        if os.path.exists(final_file):
                            z.write(final_file, arcname=os.path.basename(final_file))
            zip_url = _abs_url(request, f"/media/exports/{zip_name}")

        return JSONResponse({"ok": True, "items": results, "zip_url": zip_url})
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)
    finally:
        try:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
