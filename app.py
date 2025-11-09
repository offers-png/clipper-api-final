# app.py — ClipForge AI Backend (Stable, Single-File, Supabase-optional)
# Features:
# - Upload or URL → auto-transcribe FULL video (Whisper)
# - Clip preview (480p fast) + optional final 1080p
# - Multi-clip with ZIP (finals) and per-clip preview links
# - Per-clip transcript on demand (no auto to save tokens)
# - Absolute URLs for all media
# - Supabase: users, videos, clips (optional; auto-skip if not configured)
# - HEAD "/" for Render health checks
# - Safe on Render persistent disk (/data)

import os, json, shutil, asyncio, subprocess, tempfile
from datetime import datetime
from typing import Optional, List, Tuple
from zipfile import ZipFile

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import requests
from openai import OpenAI
from supabase import create_client, Client

# -------------------- App / Env --------------------
APP_TITLE   = "ClipForge AI Backend (Stable)"
APP_VERSION = "3.1.0"
app = FastAPI(title=APP_TITLE, version=APP_VERSION)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI() if OPENAI_API_KEY else None  # we check at call sites

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
PUBLIC_BASE  = os.getenv("PUBLIC_BASE", "").rstrip("/")

DEV_ALLOW    = os.getenv("DEV_ALLOW", "0") == "1"
DEV_USER_ID  = os.getenv("DEV_USER_ID", "dev_user")
DEV_USER_EMAIL = os.getenv("DEV_USER_EMAIL", "dev@clipforge.local")

def sb() -> Optional[Client]:
    if not (SUPABASE_URL and SUPABASE_KEY):
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print("⚠️ Supabase init failed:", e)
        return None

BASE_DIR   = "/data"
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PREV_DIR   = os.path.join(BASE_DIR, "previews")
EXP_DIR    = os.path.join(BASE_DIR, "exports")
TMP_DIR    = "/tmp"
for d in (UPLOAD_DIR, PREV_DIR, EXP_DIR, TMP_DIR):
    os.makedirs(d, exist_ok=True)

# Static hosting for generated media
app.mount("/media/previews", StaticFiles(directory=PREV_DIR), name="previews")
app.mount("/media/exports",  StaticFiles(directory=EXP_DIR),  name="exports")

# CORS
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

# -------------------- Helpers --------------------
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
    return f"text='{t}':x=w-tw-20:y=h-th-20:fontcolor=white:fontsize=28:box=1:boxcolor=black@0.45:boxborderw=10"

def hhmmss_to_seconds(s: str) -> float:
    s = s.strip()
    parts = [float(p) for p in s.split(":")]
    if len(parts)==3: return parts[0]*3600 + parts[1]*60 + parts[2]
    if len(parts)==2: return parts[0]*60 + parts[1]
    return float(s)

def duration_from(start: str, end: str) -> str:
    d = max(0.1, hhmmss_to_seconds(end) - hhmmss_to_seconds(start))
    return str(d)

def abs_url(request: Request, path: Optional[str]) -> Optional[str]:
    if not path: return None
    if path.startswith("http://") or path.startswith("https://"): return path
    base = PUBLIC_BASE or str(request.base_url).rstrip("/")
    return f"{base}{path}"

def download_to_tmp(url: str) -> str:
    """yt-dlp for platforms; fallback to HTTP -> .mp4 path"""
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    u = (url or "").lower()
    if any(k in u for k in ["youtube","youtu.be","tiktok.com","instagram.com","facebook.com","x.com","twitter.com","soundcloud.com","vimeo.com"]):
        code, err = run(["yt-dlp","-f","mp4","-o",out,"--no-playlist","--force-overwrites",url], timeout=900)
        if code!=0 or not os.path.exists(out): raise RuntimeError(f"yt-dlp failed: {err[:500]}")
    else:
        r = requests.get(url, stream=True, timeout=60)
        if r.status_code!=200: raise RuntimeError(f"HTTP {r.status_code} fetching URL")
        with open(out,"wb") as f:
            for chunk in r.iter_content(1024*1024): f.write(chunk)
    return out

# -------------------- Health --------------------
@app.get("/")
def health_get():
    return {"ok": True, "service": APP_TITLE, "version": APP_VERSION}

@app.head("/")
def health_head():
    return Response(status_code=200)

@app.get("/api/health")
def health_api():
    return {"ok": True}

# -------------------- Supabase helpers (optional) --------------------
def sb_insert(table: str, payload: dict):
    s = sb()
    if not s: return None
    try:
        return s.table(table).insert(payload).execute()
    except Exception as e:
        print(f"⚠️ Supabase insert into {table} failed:", e)
        return None

def sb_upsert_user(user_id: str, email: str):
    s = sb()
    if not s: return None
    try:
        return s.table("users").upsert({"id": user_id, "email": email}).execute()
    except Exception as e:
        print("⚠️ users upsert failed:", e)
        return None

# -------------------- Transcribe (FULL video auto) --------------------
@app.post("/transcribe")
async def transcribe(
    url: str = Form(None),
    file: UploadFile = File(None),
    user_id: str = Form(None),
    user_email: str = Form(None),
):
    if not client: return JSONResponse({"ok": False, "error": "OPENAI_API_KEY missing"}, 500)
    if DEV_ALLOW:
        user_id = user_id or DEV_USER_ID
        user_email = user_email or DEV_USER_EMAIL

    tmp = None
    audio_mp3 = None
    try:
        # resolve local media
        if file:
            suffix = os.path.splitext(file.filename)[1] or ".mp4"
            tmp = os.path.join(TMP_DIR, f"upl_{nowstamp()}{suffix}")
            with open(tmp,"wb") as f: f.write(await file.read())
        elif url:
            tmp = download_to_tmp(url)
        else:
            return JSONResponse({"ok": False, "error": "Provide file or url."}, 400)

        # convert audio to mp3 (faster for Whisper)
        audio_mp3 = tmp.rsplit(".",1)[0] + ".mp3"
        code, err = run(["ffmpeg","-y","-i",tmp,"-vn","-acodec","libmp3lame","-b:a","192k",audio_mp3], timeout=900)
        if code!=0 or not os.path.exists(audio_mp3):
            return JSONResponse({"ok": False, "error": f"FFmpeg audio convert failed: {err[:200]}."}, 500)

        # whisper
        with open(audio_mp3,"rb") as a:
            tr = client.audio.transcriptions.create(model="whisper-1", file=a, response_format="text")
        full_text = tr.strip() if isinstance(tr,str) else str(tr)

        # persist uploaded source for clipping
        src_name = f"video_{nowstamp()}.mp4"
        src_path = os.path.join(UPLOAD_DIR, src_name)
        shutil.copy(tmp, src_path)

        # save to supabase (optional)
        sb_upsert_user(user_id or "", user_email or "")
        sb_insert("videos", {
            "user_id": user_id, "email": user_email,
            "src_url": f"/media/uploads/{src_name}",
            "transcript": full_text, "created_at": datetime.utcnow().isoformat()
        })

        return JSONResponse({
            "ok": True,
            "video_path": f"/media/uploads/{src_name}",
            "transcript": full_text
        })
    except Exception as e:
        print("❌ /transcribe error:", e)
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    finally:
        for p in (tmp, audio_mp3):
            try:
                if p and os.path.exists(p): os.remove(p)
            except: pass

# -------------------- Clip core --------------------
async def build_clip(source_path: str, start: str, end: str,
                     want_preview: bool, want_final: bool,
                     wm_text: Optional[str]) -> dict:
    base = safe(os.path.splitext(os.path.basename(source_path))[0])
    stamp = nowstamp()
    dur = duration_from(start, end)

    prev_name  = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_prev_{stamp}.mp4"
    final_name = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_1080_{stamp}.mp4"
    prev_out   = os.path.join(PREV_DIR, prev_name)
    final_out  = os.path.join(EXP_DIR,  final_name)

    # preview (fast, no re-encode when no WM)
    if want_preview and not wm_text:
        code, err = run(["ffmpeg","-hide_banner","-loglevel","error",
                        "-ss",start,"-t",dur,"-i",source_path,
                        "-c","copy","-movflags","+faststart","-y",prev_out], timeout=300)
        if code!=0 or not os.path.exists(prev_out):
            # fallback re-encode
            code, err = run(["ffmpeg","-hide_banner","-loglevel","error",
                            "-ss",start,"-t",dur,"-i",source_path,
                            "-c:v","libx264","-preset","veryfast","-crf","28",
                            "-c:a","aac","-b:a","128k","-movflags","+faststart","-y",prev_out], timeout=600)
            if code!=0 or not os.path.exists(prev_out):
                raise RuntimeError(f"Preview failed: {err[:400]}")
    elif want_preview and wm_text:
        code, err = run(["ffmpeg","-hide_banner","-loglevel","error",
                        "-ss",start,"-t",dur,"-i",source_path,
                        "-c:v","libx264","-preset","veryfast","-crf","26",
                        "-c:a","aac","-b:a","128k",
                        *compose_vf(scale_filter(480), drawtext_expr(wm_text)),
                        "-movflags","+faststart","-y",prev_out], timeout=900)
        if code!=0 or not os.path.exists(prev_out):
            raise RuntimeError(f"Preview WM failed: {err[:400]}")

    if want_final:
        code, err = run(["ffmpeg","-hide_banner","-loglevel","error",
                        "-ss",start,"-t",dur,"-i",source_path,
                        "-c:v","libx264","-preset","faster","-crf","20",
                        "-c:a","aac","-b:a","192k",
                        *compose_vf(scale_filter(1080), drawtext_expr(wm_text) if wm_text else None),
                        "-movflags","+faststart","-y",final_out], timeout=1800)
        if code!=0 or not os.path.exists(final_out):
            raise RuntimeError(f"Final export failed: {err[:400]}")

    return {
        "preview_rel": f"/media/previews/{os.path.basename(prev_out)}" if want_preview else None,
        "final_rel":   f"/media/exports/{os.path.basename(final_out)}"  if want_final  else None,
    }

# Single clip (returns absolute URLs)
@app.post("/clip_preview")
async def clip_preview(
    request: Request,
    file: UploadFile = File(None),
    url: str = Form(None),
    start: str = Form(...),
    end: str   = Form(...),
    watermark: str = Form("0"),
    wm_text: str   = Form("@ClippedBySal"),
    final_1080: str = Form("0"),
):
    try:
        if file is not None:
            src = os.path.join(UPLOAD_DIR, safe(file.filename))
            with open(src,"wb") as f: shutil.copyfileobj(file.file, f)
        elif url:
            tmp = download_to_tmp(url)
            src = os.path.join(UPLOAD_DIR, f"remote_{nowstamp()}.mp4")
            shutil.copy(tmp, src); os.remove(tmp)
        else:
            return JSONResponse({"ok": False, "error": "Provide file or url."}, 400)

        r = await build_clip(
            src, start.strip(), end.strip(),
            want_preview=True,
            want_final=(final_1080=="1"),
            wm_text=(wm_text if watermark=="1" else None)
        )
        return JSONResponse({
            "ok": True,
            "preview_url": abs_url(request, r["preview_rel"]),
            "final_url":   abs_url(request, r["final_rel"]),
            "start": start, "end": end
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

# Multi-clip (array of items + optional ZIP of finals)
@app.post("/clip_multi")
async def clip_multi(
    request: Request,
    file: UploadFile = File(None),
    url: str = Form(None),
    sections: str = Form(...),    # JSON list of {start,end}
    watermark: str = Form("0"),
    wm_text: str   = Form("@ClippedBySal"),
    preview_480: str = Form("1"),
    final_1080: str  = Form("0"),
):
    tmp = None
    try:
        if file is not None:
            src = os.path.join(UPLOAD_DIR, safe(file.filename))
            with open(src,"wb") as f: f.write(await file.read())
        elif url:
            tmp = download_to_tmp(url)
            src = os.path.join(UPLOAD_DIR, f"remote_{nowstamp()}.mp4")
            shutil.copy(tmp, src)
        else:
            return JSONResponse({"ok": False, "error": "Provide file or url."}, 400)

        try:
            segs = json.loads(sections)
        except Exception:
            return JSONResponse({"ok": False, "error": "sections must be JSON list"}, 400)
        if not isinstance(segs, list) or not segs:
            return JSONResponse({"ok": False, "error": "sections must be non-empty list"}, 400)

        wm = (wm_text if watermark=="1" else None)
        want_prev  = (preview_480=="1")
        want_final = (final_1080=="1")

        sem = asyncio.Semaphore(3)
        async def worker(s, e):
            async with sem:
                rr = await build_clip(src, s.strip(), e.strip(), want_prev, want_final, wm)
                return {
                    "preview_url": abs_url(request, rr["preview_rel"]),
                    "final_url":   abs_url(request, rr["final_rel"]),
                    "start": s, "end": e
                }

        items = await asyncio.gather(*[worker(x.get("start",""), x.get("end","")) for x in segs])

        zip_url = None
        if want_final:
            zip_name = f"clips_{nowstamp()}.zip"
            zip_path = os.path.join(EXP_DIR, zip_name)
            with ZipFile(zip_path,"w") as z:
                for it in items:
                    if it["final_url"]:
                        p = os.path.join(EXP_DIR, os.path.basename(it["final_url"]))
                        if os.path.exists(p): z.write(p, arcname=os.path.basename(p))
            zip_url = abs_url(request, f"/media/exports/{zip_name}")

        return JSONResponse({"ok": True, "items": items, "zip_url": zip_url})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    finally:
        try:
            if tmp and os.path.exists(tmp): os.remove(tmp)
        except: pass

# Per-clip transcript on demand
@app.post("/clip_transcript")
async def clip_transcript(
    file_url: str = Form(...),   # absolute or relative preview/final URL
):
    if not client: return JSONResponse({"ok": False, "error": "OPENAI_API_KEY missing"}, 500)
    try:
        # fetch to tmp
        path = download_to_tmp(file_url) if file_url.startswith("http") else os.path.join("/", file_url.lstrip("/"))
        # ensure mp3
        mp3 = path.rsplit(".",1)[0] + ".mp3"
        code, err = run(["ffmpeg","-y","-i",path,"-vn","-acodec","libmp3lame","-b:a","160k",mp3], timeout=600)
        if code!=0 or not os.path.exists(mp3): return JSONResponse({"ok": False, "error": "FFmpeg audio convert failed"}, 500)
        with open(mp3,"rb") as a:
            tr = client.audio.transcriptions.create(model="whisper-1", file=a, response_format="text")
        text = tr.strip() if isinstance(tr,str) else str(tr)
        return JSONResponse({"ok": True, "text": text})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

# -------------------- AI helpers --------------------
SYSTEM_PROMPT = ("You are ClipForge AI, an editing copilot. Be concise and practical. "
                 "When asked to find moments, suggest 10–45s ranges using HH:MM:SS.")

@app.post("/ai_chat")
async def ai_chat(user_message: str = Form(...), transcript: str = Form(""), history: str = Form("[]")):
    if not client: return JSONResponse({"ok": False, "error": "OPENAI_API_KEY missing"}, 500)
    msgs = [{"role":"system","content":SYSTEM_PROMPT}]
    if transcript: msgs.append({"role":"system","content":f"Transcript:\n{transcript[:12000]}"} )
    try:
        prev = json.loads(history)
        if isinstance(prev,list): msgs += prev
    except: pass
    msgs.append({"role":"user","content":user_message})
    r = client.chat.completions.create(model="gpt-4o-mini", temperature=0.3, messages=msgs)
    return JSONResponse({"ok": True, "reply": r.choices[0].message.content.strip()})
