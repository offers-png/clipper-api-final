# app.py

import os, sys, json, shutil, asyncio, subprocess, tempfile

# Windows consoles and redirected logs default to cp1252; emoji log lines in
# this app must be escaped, not crash startup or requests.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="backslashreplace")

from datetime import datetime
from typing import Optional, List, Tuple
from zipfile import ZipFile

from fastapi import Response
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from openai import OpenAI
from db_history import insert_transcript
from supabase import create_client, Client
from stripe_billing import check_clip_access, record_clip_used, create_checkout_session, handle_webhook
import requests
from openai_audio import transcribe as transcribe_media
from url_media import download, MediaError, MediaTooLarge, UnsupportedSource,     check_transcribe_url, check_media_limits, size_message
from clipforge_studios.flags import studios_enabled
from billing_identity import billing_actor, checkout_reference
from billing_state import VerificationError
from owner_access import is_owner
from rate_limit import RateLimiter

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

# Default: 30 OpenAI-backed requests per user per 10 minutes.
ai_limiter = RateLimiter(int(os.getenv("CLIPFORGE_AI_RATE_LIMIT", "30")),
                         float(os.getenv("CLIPFORGE_AI_RATE_WINDOW_SECONDS", "600")))

def ai_actor(request: Request) -> dict:
    """Signed-in user, OpenAI configured, and within the per-user AI rate limit."""
    actor = billing_actor(request)
    if not OPENAI_API_KEY:
        raise HTTPException(503, "AI features are not configured on this server (OPENAI_API_KEY is not set).")
    retry = ai_limiter.hit(actor["id"])
    if retry:
        raise HTTPException(429, f"Too many AI requests. Try again in {retry} seconds.",
                            headers={"Retry-After": str(retry)})
    return actor

def sb():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

BASE_DIR   = "/tmp/clipforge"
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PREVIEW_DIR= os.path.join(BASE_DIR, "previews")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
THUMB_DIR  = os.path.join(BASE_DIR, "thumbs")
TMP_DIR    = "/tmp"
for d in (UPLOAD_DIR, PREVIEW_DIR, EXPORT_DIR, THUMB_DIR):
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
        "https://clipforge.dealdily.com",
        "https://clipforge-app.netlify.app",
        "http://localhost:5173",
        "capacitor://localhost",
        "http://localhost"
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

def friendly_err(err: str, context: str = "Processing") -> str:
    """Extract just the last meaningful error line from ffmpeg output."""
    lines = [l.strip() for l in err.splitlines() if l.strip()]
    # Find lines with actual error content, skip version/config spam
    error_lines = [l for l in lines if any(k in l.lower() for k in ["error", "invalid", "no such", "failed", "cannot", "unable"])]
    if error_lines:
        return f"{context} failed: {error_lines[-1][:120]}"
    return f"{context} failed. Check your video file and try again."
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

def download_to_tmp(url: str, max_bytes=None, max_seconds=None) -> str:
    return download(url, max_bytes, max_seconds)

# /transcribe limits (defaults: 500 MB, 30 minutes).
TRANSCRIBE_MAX_BYTES = int(float(os.getenv("CLIPFORGE_TRANSCRIBE_MAX_MB", "500")) * 1024 * 1024)
TRANSCRIBE_MAX_SECONDS = float(os.getenv("CLIPFORGE_TRANSCRIBE_MAX_MINUTES", "30")) * 60

def own_media_file(url: str) -> Optional[str]:
    """This server's /media/previews or /media/exports URL as an existing local file.
    Resolved by file name only; never fetched over the network."""
    from urllib.parse import urlparse
    path = urlparse(url.strip()).path
    for prefix, folder in (("/media/previews/", PREVIEW_DIR), ("/media/exports/", EXPORT_DIR)):
        name = path[len(prefix):] if path.startswith(prefix) else ""
        if name and name not in (".", "..") and name == os.path.basename(name):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                return candidate
    return None

async def save_upload(file: UploadFile, dest: str, max_bytes: int):
    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise MediaTooLarge(size_message(max_bytes))
            f.write(chunk)

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
        raise RuntimeError(friendly_err(err, "Thumbnail"))

@app.get("/")
def health_get():
    return {"ok": True, "service": APP_TITLE, "version": APP_VERSION}


# ── Stripe endpoints ──────────────────────────────────────────────────────────

@app.post("/billing/checkout")
async def billing_checkout(request: Request):
    actor = billing_actor(request)
    from owner_access import is_owner
    if actor.get('dev_bypass'):
        return JSONResponse({'ok':False, 'error':'Checkout is not available in local test mode. No payment was initiated.'},409)
    if is_owner(actor):return {'ok':True,'already_subscribed':True,'owner_development':True}
    try:
        url = create_checkout_session(actor['email'])
        return {'ok':True, 'url':url}
    except VerificationError as error:
        if str(error)=='already_subscribed':
            return {'ok':True, 'already_subscribed':True}
        return JSONResponse({'ok':False, 'error':'Subscription verification is required before checkout. No new subscription was created.'},409)
    except Exception:
        return JSONResponse({'ok':False, 'error':'Checkout is temporarily unavailable. No payment was initiated.'},503)


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    result = handle_webhook(payload, sig_header)
    if not result.get("ok"):
        return JSONResponse(result, result.get("status", 400))
    return result


@app.post("/billing/check-access")
async def billing_check_access(request: Request):
    from owner_access import development_access
    actor = billing_actor(request)
    session_id = await checkout_reference(request)
    if session_id and (not isinstance(session_id,str) or not session_id.startswith('cs_') or len(session_id)>255):
        return JSONResponse({'ok':False, 'error':'Invalid checkout reference'},400)
    result = development_access(actor)
    if result is None:result = await asyncio.to_thread(check_clip_access, actor['email'], checkout_id=session_id)
    if result.get('plan')=='unknown':
        return JSONResponse({'ok':False, **result, 'error':'We could not verify your subscription. Retry verification; do not pay again.'},503)
    return {'ok':True, **result}

# ─────────────────────────────────────────────────────────────────────────────

@app.get("/dev/bypass")
def dev_bypass_status(request: Request):
    """Lets a localhost frontend know local test mode is on; always false on Render."""
    from dev_bypass import dev_actor
    actor = dev_actor(request)
    return {"enabled": bool(actor), "email": actor["email"] if actor else None}


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
                raise RuntimeError(friendly_err(err, "Clip preview"))
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
            raise RuntimeError(friendly_err(err, "Clip preview"))

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
            raise RuntimeError(friendly_err(err, "Final export"))

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
    user_id: str = Form(default="anonymous"),
):
    from owner_access import development_access,is_owner
    actor = billing_actor(request)
    user_id = actor['email']
    tmp = None
    try:
        # ── Access gate ──────────────────────────────────────────────────────
        if user_id and user_id != "anonymous":
            access = development_access(actor) or check_clip_access(user_id)
            if not access.get("allowed"):
                if access.get('existing_subscription') and access.get('subscription_status')=='trialing':
                    return JSONResponse({'ok':False,'upgrade':False,
                        'error':'Your Stripe trial is active. The daily trial clip limit has been reached; no new subscription is needed.'},409)
                if access.get('plan') in ('unknown', 'payment_pending'):
                    return JSONResponse({'ok':False, 'upgrade':False,
                        'error':'Your subscription needs verification. Please retry or contact support; do not pay again.'},503 if access.get('plan')=='unknown' else 409)
                reason = access.get("reason", "unknown")
                msg = {
                    "trial_expired": "Your 7-day free trial has ended. Upgrade to keep clipping.",
                    "daily_limit":   f"You've used all {3} free clips for today. Upgrade for unlimited clips, or come back tomorrow.",
                    "cancelled":     "Your subscription is cancelled. Reactivate to continue.",
                    "expired":       "Your subscription has expired. Upgrade to continue.",
                }.get(reason, "Upgrade required to continue clipping.")
                return JSONResponse({"ok": False, "error": msg, "upgrade": True}, 403)
        # ─────────────────────────────────────────────────────────────────────

        if file is not None:
            src = os.path.join(UPLOAD_DIR, safe(file.filename))
            with open(src, "wb") as f:
                f.write(await file.read())
        elif url:
            tmp = await asyncio.to_thread(download_to_tmp, url)
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

        # Save clip job to history
        record_id = None
        try:
            source_name = file.filename if file else (url or "unknown")
            preview_urls = [r["preview_url"] for r in results if r.get("preview_url")]
            record_id = insert_transcript(
                user_id=user_id,
                source_name=source_name,
                transcript=f"Clipped {len(results)} segment(s): " + ", ".join(
                    f"{r['start']}→{r['end']}" for r in results
                ),
                preview_url=preview_urls[0] if preview_urls else None,
            )
        except Exception as db_err:
            print(f"⚠️ History save failed (clip_multi): {db_err}")

        # Record usage for billing
        if user_id and user_id != "anonymous" and not is_owner(actor):
            try:
                record_clip_used(user_id)
            except Exception as e:
                print(f"⚠️ record_clip_used failed: {e}")

        return JSONResponse({"ok": True, "items": results, "zip_url": zip_url, "record_id": record_id})
    except MediaError as e:
        return JSONResponse({"ok": False, "error": str(e), "upgrade": False}, 422)
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
    ai_actor(request)
    form = await request.form()
    clip_url = form.get("clip_url", "")

    if not clip_url:
        return {"ok": False, "error": "clip_url is required"}

    # Extract filename
    filename = clip_url.split("/")[-1]

    # Build possible paths
    preview_path = os.path.join(PREVIEW_DIR, filename)
    final_path   = os.path.join(EXPORT_DIR, filename)

    # Decide which file exists
    if os.path.exists(final_path):
        clip_path = final_path
    elif os.path.exists(preview_path):
        clip_path = preview_path
    else:
        return {"ok": False, "error": f"Clip not found on server: {filename}"}

    # Mono 32 kbps, split into parts if still over OpenAI's upload limit.
    try:
        text, segments = await asyncio.to_thread(transcribe_media, client, clip_path)
    except MediaError as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "text": text, "segments": segments}



@app.post("/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(None),
    url: str = Form(None),
):
    # History is saved under the signed-in account, never a client-supplied id.
    user_id = ai_actor(request)["email"]
    tmp = None
    src = None
    kept = None  # this server's own preview/export file; never deleted here
    source_name = None

    try:
        # 1) URL transcription: this server's clips, or YouTube/TikTok/Instagram
        if url:
            kept = own_media_file(url)
            if not kept:
                await asyncio.to_thread(check_transcribe_url, url)
                tmp = await asyncio.to_thread(download_to_tmp, url, TRANSCRIBE_MAX_BYTES, TRANSCRIBE_MAX_SECONDS)
            source_name = url.split("/")[-1] if "/" in url else url

        # 2) File upload transcription
        elif file:
            filename = safe(file.filename or f"upload_{nowstamp()}.mp4")
            src = os.path.join(UPLOAD_DIR, filename)
            source_name = file.filename
            await save_upload(file, src, TRANSCRIBE_MAX_BYTES)

        else:
            return JSONResponse(
                {"ok": False, "error": "Provide a file or a url."},
                status_code=400,
            )

        media = kept or tmp or src
        await asyncio.to_thread(check_media_limits, media, TRANSCRIBE_MAX_BYTES, TRANSCRIBE_MAX_SECONDS)

        # Mono 32 kbps, split into parts with shifted timestamps if still over
        # OpenAI's upload limit. Rejects media without an audio track.
        text, segments = await asyncio.to_thread(transcribe_media, client, media)

        # ✅ Save to database
        record_id = None
        try:
            record_id = insert_transcript(
               user_id=user_id,
               source_name=source_name or "unknown",
               transcript=text,
               duration=None,
               preview_url=None,
               final_url=None,
            )
            if record_id:
                print(f"✅ Transcript saved to database for user: {user_id}, id: {record_id}")
            else:
                print(f"⚠️ Failed to save transcript to database for user: {user_id}")

        except Exception as db_err:
            print(f"❌ Database error: {str(db_err)}")

    # Don't fail the whole request if DB save fails

        return JSONResponse({"ok": True, "text": text, "segments": segments,
                             "saved_to_db": bool(record_id), "record_id": record_id})

    except UnsupportedSource as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    except MediaTooLarge as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=413)

    except MediaError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=422)

    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"Transcription failed: {str(e)}"},
            status_code=500,
        )

    finally:
        for p in [tmp, src]:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


@app.post("/ask-ai")
async def ask_ai(request: Request):
    ai_actor(request)
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
async def data_upload(request: Request, file: UploadFile = File(...)):
    # Replaces the server-wide downloader cookies, so owner/admin only.
    if not is_owner(billing_actor(request)):
        raise HTTPException(403, "Admin access required")
    try:
        dest_path = "/tmp/cookies.txt"
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
        ("/media/exports/",  EXPORT_DIR),
        ("/media/thumbs/",   THUMB_DIR),
    ]:
        if prefix in url:
            filename = url.split(prefix)[-1]
            return os.path.join(folder, filename)

    return None


# ======================================
# TRANSCRIBE CLIPPED VIDEO (FAST + NO TIMEOUTS)
# ======================================

@app.post("/ai_chat")
async def ai_chat(request: Request):
    ai_actor(request)
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

@app.get("/history/{user_id}")
async def get_history(user_id: str, request: Request, limit: int = 10):
    # History rows are keyed by the account email (see clip_multi/transcribe).
    email = billing_actor(request)["email"]
    if user_id.casefold() != email.casefold():
        raise HTTPException(403, "You can only view your own history")
    from db_history import get_db
    db = get_db()
    if not db:
        return JSONResponse({"ok": False, "error": "Database unavailable"}, 500)
    res = db.table("history").select("*").eq("user_id", email).order("created_at", desc=True).limit(limit).execute()
    return {"ok": True, "history": res.data or []}


async def update_own_record(request: Request, record_id: str, fields: dict):
    """Update a history row only when it belongs to the signed-in account."""
    email = billing_actor(request)["email"]
    from db_history import get_db

    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Database unavailable")

    data = {key: value for key, value in fields.items() if value is not None}
    if not data:
        return {"ok": False, "message": "Nothing to update"}

    await asyncio.sleep(0.5)
    res = db.table("history").update(data).eq("id", record_id).eq("user_id", email).execute()
    if not res.data:
        raise HTTPException(404, "Record not found")
    return {"ok": True, "updated": list(data.keys())}


@app.post("/history/update")
async def update_history(
    request: Request,
    record_id: str = Form(...),
    hooks: Optional[str] = Form(None),
    hashtags: Optional[str] = Form(None),
    summary: Optional[str] = Form(None),
    final_url: Optional[str] = Form(None),
    titles: Optional[str] = Form(None),
):
    return await update_own_record(request, record_id, dict(
        hooks=hooks, hashtags=hashtags, summary=summary, final_url=final_url, titles=titles))


@app.post("/save_ai_insights")
async def save_ai_insights(
    request: Request,
    record_id: str = Form(...),
    hooks: Optional[str] = Form(None),
    hashtags: Optional[str] = Form(None),
    summary: Optional[str] = Form(None),
    titles: Optional[str] = Form(None),
    final_url: Optional[str] = Form(None),
):
    return await update_own_record(request, record_id, dict(
        hooks=hooks, hashtags=hashtags, summary=summary, titles=titles, final_url=final_url))

# Additive opt-in local Studios; disabled unless explicitly configured.
if studios_enabled():
    from clipforge_studios.install import install as install_studios
    install_studios(app)


@app.get("/api/studios/status")
def studio_runtime_status():
    """Public switches and executable availability only; no secrets or paths."""
    from importlib.metadata import version, PackageNotFoundError
    try:
        downloader = version("yt-dlp")
    except PackageNotFoundError:
        downloader = None
    return {"enabled": studios_enabled(),
            "mounted": any(getattr(route, "path", None) == "/studios-api" for route in app.routes),
            "ffmpeg_available": bool(shutil.which("ffmpeg")),
            "ffprobe_available": bool(shutil.which("ffprobe")),
            "downloader_version": downloader}
