# app.py

import os, json, shutil, asyncio, subprocess, tempfile
from datetime import datetime
from typing import Optional, List, Tuple
from zipfile import ZipFile

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from openai import OpenAI
from supabase import create_client, Client
import requests

APP_TITLE = "ClipForge AI Backend (Stable)"
APP_VERSION = "3.1.0"
app = FastAPI(title=APP_TITLE, version=APP_VERSION)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI() if OPENAI_API_KEY else None

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "transcriptions").strip()
SUPABASE_TEXT_COL_PRIMARY = os.getenv("SUPABASE_TEXT_COL", "text").strip()
SUPABASE_TEXT_COL_ALT = os.getenv("SUPABASE_TEXT_COL_ALT", "content").strip()

def sb():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

BASE_DIR   = "/data"
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PREVIEW_DIR= os.path.join(BASE_DIR, "previews")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
THUMB_DIR  = os.path.join(BASE_DIR, "thumbs")
TMP_DIR    = "/tmp"
for d in (UPLOAD_DIR, PREVIEW_DIR, EXPORT_DIR, THUMB_DIR, TMP_DIR):
    os.makedirs(d, exist_ok=True)

app.mount("/media/previews", StaticFiles(directory=PREVIEW_DIR), name="previews")
app.mount("/media/exports",  StaticFiles(directory=EXPORT_DIR),  name="exports")
app.mount("/media/thumbs",   StaticFiles(directory=THUMB_DIR),   name="thumbs")

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

PUBLIC_BASE = os.getenv("PUBLIC_BASE", "").rstrip("/")

def nowstamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")

def safe(name: str) -> str:
    return "".join(c for c in (name or "file") if c.isalnum() or c in ("-", "_", "."))[:120]

def run(cmd: List[str], timeout=1200) -> Tuple[int, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return p.returncode, (p.stdout + "\n" + p.stderr).strip()

def scale_filter(h: int) -> str:
    return f"scale=-2:{h}:flags=lanczos"

def compose_vf(scale: Optional[str], drawtext: Optional[str]) -> List[str]:
    if scale and drawtext: return ["-vf", f"{scale},drawtext={drawtext}"]
    if scale:              return ["-vf", scale]
    if drawtext:           return ["-vf", f"drawtext={drawtext}"]
    return []

def drawtext_expr(text: str) -> str:
    t = (text or "").replace("'", r"\'")
    return (
        f"text='{t}':x=w-tw-20:y=h-th-20:"
        "fontcolor=white:fontsize=28:box=1:boxcolor=black@0.45:boxborderw=10"
    )

def hhmmss_to_seconds(s: str) -> float:
    s = s.strip()
    parts = [float(p) for p in s.split(":")]
    if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
    if len(parts) == 2: return parts[0]*60 + parts[1]
    return float(s)

def duration_from(start: str, end: str) -> float:
    return max(0.1, hhmmss_to_seconds(end) - hhmmss_to_seconds(start))

def seconds_to_text(x: float) -> str:
    x = max(0, int(round(x)))
    h = x // 3600
    m = (x % 3600) // 60
    s = x % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def ffprobe_duration(path: str) -> Optional[float]:
    try:
        code, out = run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path
        ], timeout=30)
        if code == 0 and out.strip():
            return float(out.strip().splitlines()[-1])
    except Exception:
        pass
    return None

def file_size(path: str) -> Optional[int]:
    try: return os.path.getsize(path)
    except Exception: return None

def abs_url(request: Request, path: Optional[str]) -> Optional[str]:
    if not path: return None
    if path.startswith("http://") or path.startswith("https://"): return path
    base = PUBLIC_BASE or str(request.base_url).rstrip("/")
    return f"{base}{path}"

def download_to_tmp(url: str) -> str:
    tmp_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    u = (url or "").lower()

    if any(k in u for k in [
        "youtube", "youtu.be", "tiktok.com", "instagram.com",
        "facebook.com", "x.com", "twitter.com", "soundcloud.com", "vimeo.com"
    ]):
        # ✅ Use cookies.txt from /data to bypass bot check
        code, err = run([
            "yt-dlp",
            "--cookies", "/data/cookies.txt",
            "-f", "mp4",
            "-o", tmp_path,
            "--no-playlist",
            "--force-overwrites",
            url
        ], timeout=900)
    else:
        # Regular direct download (no cookies used)
        r = requests.get(url, stream=True, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} while fetching URL")
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
        code = 0
        err = ""

    if code != 0 or not os.path.exists(tmp_path):
        raise RuntimeError(f"yt-dlp failed: {err[:500]}")

    return tmp_path

def make_thumbnail(source_path: str, t_start: str, out_path: str):
    # Grab a frame ~0.25s after start to avoid black frames on cuts
    seek = max(0.0, hhmmss_to_seconds(t_start) + 0.25)
    code, err = run([
        "ffmpeg","-hide_banner","-loglevel","error",
        "-ss", str(seek), "-i", source_path,
        "-frames:v","1","-vf","scale=480:-1",
        "-y", out_path
    ], timeout=30)
    if code != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"thumbnail failed: {err[:300]}")

@app.get("/")
def health_get():
    return {"ok": True, "service": APP_TITLE, "version": APP_VERSION}

@app.head("/")
def health_head():
    return Response(status_code=200)

@app.get("/api/health")
def health_api():
    return {"ok": True}

async def build_clip(
    source_path: str,
    start: str,
    end: str,
    want_preview: bool,
    want_final: bool,
    watermark_text: Optional[str],
) -> dict:
    base = safe(os.path.splitext(os.path.basename(source_path))[0])
    stamp = nowstamp()
    dur_s = duration_from(start, end)

    prev_name  = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_prev_{stamp}.mp4"
    final_name = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_1080_{stamp}.mp4"
    prev_out   = os.path.join(PREVIEW_DIR, prev_name)
    final_out  = os.path.join(EXPORT_DIR,  final_name)

    # preview
    if want_preview and not watermark_text:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", str(dur_s), "-i", source_path,
            "-c","copy","-movflags","+faststart","-y", prev_out
        ], timeout=300)
        if code != 0 or not os.path.exists(prev_out):
            code, err = run([
                "ffmpeg","-hide_banner","-loglevel","error",
                "-ss", start, "-t", str(dur_s), "-i", source_path,
                "-c:v","libx264","-preset","veryfast","-crf","28",
                "-c:a","aac","-b:a","128k",
                "-movflags","+faststart","-y", prev_out
            ], timeout=600)
            if code != 0 or not os.path.exists(prev_out):
                raise RuntimeError(f"Preview failed: {err[:500]}")
    elif want_preview and watermark_text:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", str(dur_s), "-i", source_path,
            "-c:v","libx264","-preset","veryfast","-crf","26",
            "-c:a","aac","-b:a","128k",
            *compose_vf(scale_filter(480), drawtext_expr(watermark_text)),
            "-movflags","+faststart","-y", prev_out
        ], timeout=900)
        if code != 0 or not os.path.exists(prev_out):
            raise RuntimeError(f"Preview watermark failed: {err[:500]}")

    # final
    if want_final:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", str(dur_s), "-i", source_path,
            "-c:v","libx264","-preset","faster","-crf","20",
            "-c:a","aac","-b:a","192k",
            *compose_vf(scale_filter(1080), drawtext_expr(watermark_text) if watermark_text else None),
            "-movflags","+faststart","-y", final_out
        ], timeout=1800)
        if code != 0 or not os.path.exists(final_out):
            raise RuntimeError(f"Final export failed: {err[:500]}")

    # thumbnail
    thumb_name = f"{base}_{start.replace(':','-')}_{stamp}.jpg"
    thumb_out  = os.path.join(THUMB_DIR, thumb_name)
    try:
        make_thumbnail(source_path, start, thumb_out)
    except Exception as e:
        # fall back to generating from preview if source seek fails
        if os.path.exists(prev_out):
            try: make_thumbnail(prev_out, "00:00:00", thumb_out)
            except Exception as _:
                thumb_out = None
        else:
            thumb_out = None

    result = {
        "preview_path": prev_out if os.path.exists(prev_out) else None,
        "final_path":   final_out if os.path.exists(final_out) else None,
        "thumb_path":   thumb_out if thumb_out and os.path.exists(thumb_out) else None,
        "duration_seconds": dur_s,
        "start": start,
        "end": end
    }
    return result

@app.post("/clip_multi")
async def clip_multi(
    request: Request,
    file: UploadFile = File(None),
    url: str = Form(None),
    sections: str = Form(...),
    watermark: str = Form("0"),
    wm_text: str   = Form("@ClipForge"),
    preview_480: str = Form("1"),
    final_1080: str  = Form("0"),
):
    tmp = None
    try:
        if file is not None:
            src = os.path.join(UPLOAD_DIR, safe(file.filename))
            with open(src, "wb") as f:
                f.write(await file.read())
        elif url:
            tmp = download_to_tmp(url)
            src = os.path.join(UPLOAD_DIR, safe(os.path.basename(url) or f"remote_{nowstamp()}.mp4"))
            shutil.copy(tmp, src)
        else:
            return JSONResponse({"ok": False, "error": "Provide a file or a url."}, 400)

        try:
            segs = json.loads(sections)
        except Exception:
            return JSONResponse({"ok": False, "error": "sections must be valid JSON list"}, 400)
        if not isinstance(segs, list) or not segs:
            return JSONResponse({"ok": False, "error": "sections must be a non-empty list"}, 400)

        wm = (wm_text if watermark == "1" else None)
        want_prev  = (preview_480 == "1")
        want_final = (final_1080 == "1")

        sem = asyncio.Semaphore(3)
        async def worker(s, e):
            async with sem:
                r = await build_clip(src, s.strip(), e.strip(), want_prev, want_final, wm)
                return {
                    "start": s, "end": e,
                    "duration_seconds": r["duration_seconds"],
                    "duration_text": seconds_to_text(r["duration_seconds"]),
                    "preview_url": abs_url(request, f"/media/previews/{os.path.basename(r['preview_path'])}") if r["preview_path"] else None,
                    "final_url":   abs_url(request, f"/media/exports/{os.path.basename(r['final_path'])}") if r["final_path"] else None,
                    "thumb_url":   abs_url(request, f"/media/thumbs/{os.path.basename(r['thumb_path'])}") if r["thumb_path"] else None
                }

        tasks = [worker(str(s.get("start","")), str(s.get("end",""))) for s in segs]
        results = await asyncio.gather(*tasks)

        zip_url = None
        if want_final:
            zip_name = f"clips_{nowstamp()}.zip"
            zip_path = os.path.join(EXPORT_DIR, zip_name)
            with ZipFile(zip_path, "w") as z:
                for r in results:
                    if r.get("final_url"):
                        final_fp = os.path.join(EXPORT_DIR, os.path.basename(r["final_url"]))
                        if os.path.exists(final_fp):
                            z.write(final_fp, arcname=os.path.basename(final_fp))
            zip_url = abs_url(request, f"/media/exports/{zip_name}")

        return JSONResponse({"ok": True, "items": results, "zip_url": zip_url})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    finally:
        try:
            if tmp and os.path.exists(tmp): os.remove(tmp)
        except Exception: pass

 # ======================================
 # TRANSCRIBE CLIPPED VIDEO (FAST + NO TIMEOUTS)
 # ======================================
@app.post("/transcribe_clip")
async def transcribe_clip(request: Request):
    form = await request.form()
    clip_url = form.get("clip_url", "")

    if not clip_url:
        return {"ok": False, "error": "clip_url is required"}

    filename = clip_url.split("/")[-1]
    clip_path = f"/data/exports/{filename}"

    if not os.path.exists(clip_path):
        return {"ok": False, "error": f"Clip not found on server: {clip_path}"}

    # Convert to mp3
    mp3_path = clip_path.replace(".mp4", ".mp3")
    code, err = run([
        "ffmpeg", "-y", "-i", clip_path,
        "-vn", "-acodec", "libmp3lame", "-b:a", "192k",
        mp3_path
    ], timeout=60)

    if code != 0 or not os.path.exists(mp3_path):
        return {"ok": False, "error": f"FFmpeg failed: {err}"}

    # Whisper transcription
    with open(mp3_path, "rb") as a:
        tr = client.audio.transcriptions.create(
            model="whisper-1",
            file=a,
            response_format="text"
        )

    text = tr.strip() if isinstance(tr, str) else str(tr)

    try:
        os.remove(mp3_path)
    except:
        pass

    return {"ok": True, "text": text}



@app.post("/ask-ai")
async def ask_ai(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")
    transcript = body.get("transcript", "")

    if not prompt:
        return JSONResponse({"error": "Prompt is required"}, status_code=400)

    client = OpenAI(api_key=OPENAI_API_KEY)

    messages = [
        {"role": "system", "content": "You are ClipForge AI assistant. Provide summaries, hooks, moments, and titles."},
        {"role": "user", "content": f"Transcript:\n{transcript}\n\nQuestion:\n{prompt}"}
    ]

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
    )

    reply = response.choices[0].message.content.strip()
    return {"response": reply}


# -------------------------------
# Data Upload Route
# -------------------------------
@app.post("/data-upload")
async def data_upload(file: UploadFile = File(...)):
    try:
        dest_path = "/data/cookies.txt"
        contents = await file.read()

        if not contents.strip():
            return {"ok": False, "error": "Uploaded file is empty"}

        with open(dest_path, "wb") as f:
            f.write(contents)

        with open(dest_path, "rb") as f:
            first_line = f.readline().decode(errors="ignore").strip()

        if "Netscape" not in first_line:
            return {
                "ok": False,
                "error": "Invalid cookies format. Must start with '# Netscape HTTP Cookie File'."
            }

        return {"ok": True, "path": dest_path}

    except Exception as e:
        return {"ok": False, "error": str(e)}

def resolve_local_media_path(url: str):
    """
    If the URL points to our own /media/... storage, convert it to a direct filesystem path.
    """
    if not url:
        return None

    for prefix, folder in [
        ("/media/previews/", PREVIEW_DIR),
        ("/media/exports/", EXPORT_DIR),
        ("/media/thumbs/", THUMB_DIR),
    ]:
        if prefix in url:
            filename = url.split(prefix)[-1]
            return os.path.join(folder, filename)

    return None


# ======================================
# TRANSCRIBE CLIPPED VIDEO (FAST + NO TIMEOUTS)
# ======================================

@app.post("/transcribe_clip")
async def transcribe_clip(request: Request):
    form = await request.form()
    clip_url = form.get("clip_url", "")

    if not clip_url:
        return {"ok": False, "error": "clip_url is required"}

    # Extract filename from URL
    filename = clip_url.split("/")[-1]
    clip_path = f"/data/exports/{filename}"

    if not os.path.exists(clip_path):
        return {"ok": False, "error": f"Clip not found on server: {clip_path}"}

    # Convert clip to mp3
    mp3_path = clip_path.replace(".mp4", ".mp3")
    code, err = run([
        "ffmpeg", "-y", "-i", clip_path,
        "-vn", "-acodec", "libmp3lame", "-b:a", "192k",
        mp3_path
    ], timeout=60)

    if code != 0 or not os.path.exists(mp3_path):
        return {"ok": False, "error": f"FFmpeg failed: {err}"}

    # Whisper transcription
    with open(mp3_path, "rb") as a:
        tr = client.audio.transcriptions.create(
            model="whisper-1",
            file=a,
            response_format="text"
        )

    text = tr.strip() if isinstance(tr, str) else str(tr)

    # Cleanup small mp3
    try:
        os.remove(mp3_path)
    except:
        pass

    return {"ok": True, "text": text}


# ======================================
# AI CHAT ENDPOINT (FINAL + WORKING)
# ======================================

from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

@app.post("/ai_chat")
async def ai_chat(request: Request):
    form = await request.form()

    user_message = form.get("user_message", "")
    transcript = form.get("transcript", "")
    history_json = form.get("history", "[]")

    # Parse chat history safely
    try:
        history = json.loads(history_json)
    except:
        history = []

    # Build messages array
    messages = []

    # Add transcript context first
    if transcript:
        messages.append({
            "role": "system",
            "content": f"Transcript:\n{transcript}"
        })

    # Add previous messages
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})

    # Add new user message
    messages.append({"role": "user", "content": user_message})

    # OpenAI API call (correct format)
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages
    )

    # Correct extraction for new API
    reply_text = completion.choices[0].message.content

    return {"ok": True, "reply": reply_text}
