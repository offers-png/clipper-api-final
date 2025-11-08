# app.py — ClipForge AI Backend (Stable, Single-File, Supabase-optional)
# - URL + file transcription (robust; fixes moov-atom issues)
# - Preview 480p + optional Final 1080p clips (file OR URL)
# - Multi-clip + ZIP
# - AI chat + auto-clip
# - Absolute URLs returned for frontend
# - Supabase save: on; auto-skip if not configured; retries alt column ('content') if 'text' missing

import os, json, shutil, asyncio, subprocess, glob, tempfile
from datetime import datetime
from typing import Optional, List, Tuple
from zipfile import ZipFile

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from openai import OpenAI
from supabase import create_client, Client
import requests

# =========================
# App / Env
# =========================
APP_TITLE = "ClipForge AI Backend (Stable)"
APP_VERSION = "3.0.0"
app = FastAPI(title=APP_TITLE, version=APP_VERSION)
client = OpenAI()  # requires OPENAI_API_KEY

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "transcriptions").strip()  # table name can be overridden
SUPABASE_TEXT_COL_PRIMARY = os.getenv("SUPABASE_TEXT_COL", "text").strip()
SUPABASE_TEXT_COL_ALT = os.getenv("SUPABASE_TEXT_COL_ALT", "content").strip()  # fallback column if 'text' not found

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print("⚠️ Supabase init failed:", e)
        supabase = None

BASE_DIR = "/data"
UPLOAD_DIR  = os.path.join(BASE_DIR, "uploads")
PREVIEW_DIR = os.path.join(BASE_DIR, "previews")
EXPORT_DIR  = os.path.join(BASE_DIR, "exports")
TMP_DIR     = "/tmp"
for d in (UPLOAD_DIR, PREVIEW_DIR, EXPORT_DIR, TMP_DIR):
    os.makedirs(d, exist_ok=True)

# Static hosting
app.mount("/media/previews", StaticFiles(directory=PREVIEW_DIR), name="previews")
app.mount("/media/exports",  StaticFiles(directory=EXPORT_DIR),  name="exports")

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

PUBLIC_BASE = os.getenv("PUBLIC_BASE", "").rstrip("/")

# =========================
# Helpers
# =========================
def nowstamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")

def safe(name: str) -> str:
    return "".join(c for c in (name or "file") if c.isalnum() or c in ("-", "_", "."))[:120]

def run(cmd: List[str], timeout=1200) -> Tuple[int, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    # ffprobe writes to stdout; ffmpeg to stderr; combine so we can always read something
    return p.returncode, (p.stdout + "\n" + p.stderr).strip()

def scale_filter(h: int) -> str:
    return f"scale=-2:{h}:flags=lanczos"

def compose_vf(scale: Optional[str], drawtext: Optional[str]) -> List[str]:
    if scale and drawtext:
        return ["-vf", f"{scale},drawtext={drawtext}"]
    if scale:
        return ["-vf", scale]
    if drawtext:
        return ["-vf", f"drawtext={drawtext}"]
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

def duration_from(start: str, end: str) -> str:
    d = max(0.1, hhmmss_to_seconds(end) - hhmmss_to_seconds(start))
    return str(d)

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
    try:
        return os.path.getsize(path)
    except Exception:
        return None

def abs_url(request: Request, path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = PUBLIC_BASE or str(request.base_url).rstrip("/")
    return f"{base}{path}"

def download_to_tmp(url: str) -> str:
    """
    Robust remote downloader:
    - Use yt-dlp for major platforms
    - Fallback to direct HTTP stream
    Returns a local .mp4 file path
    """
    tmp_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    u = (url or "").lower()
    if any(k in u for k in ["youtube", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "soundcloud.com", "vimeo.com"]):
        code, err = run(["yt-dlp", "-f", "mp4", "-o", tmp_path, "--no-playlist", "--force-overwrites", url], timeout=900)
        if code != 0 or not os.path.exists(tmp_path):
            raise RuntimeError(f"yt-dlp failed: {err[:500]}")
    else:
        r = requests.get(url, stream=True, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} while fetching URL")
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
    return tmp_path

# =========================
# Health
# =========================
@app.get("/")
def health():
    return {"ok": True, "service": APP_TITLE, "version": APP_VERSION}

# =========================
# Clip core
# =========================
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
    dur = duration_from(start, end)

    prev_name  = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_prev_{stamp}.mp4"
    final_name = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_1080_{stamp}.mp4"
    prev_out   = os.path.join(PREVIEW_DIR, prev_name)
    final_out  = os.path.join(EXPORT_DIR, final_name)

    # Fast preview (stream copy) if no watermark
    if want_preview and not watermark_text:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", dur, "-i", source_path,
            "-c","copy","-movflags","+faststart","-y", prev_out
        ], timeout=300)
        if (code != 0) or (not os.path.exists(prev_out)):
            # fallback to quick encode
            code, err = run([
                "ffmpeg","-hide_banner","-loglevel","error",
                "-ss", start, "-t", dur, "-i", source_path,
                "-c:v","libx264","-preset","veryfast","-crf","28",
                "-c:a","aac","-b:a","128k",
                "-movflags","+faststart","-y", prev_out
            ], timeout=600)
            if (code != 0) or (not os.path.exists(prev_out)):
                raise RuntimeError(f"Preview failed: {err[:500]}")

    # Preview with watermark (needs encode)
    elif want_preview and watermark_text:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", dur, "-i", source_path,
            "-c:v","libx264","-preset","veryfast","-crf","26",
            "-c:a","aac","-b:a","128k",
            *compose_vf(scale_filter(480), drawtext_expr(watermark_text)),
            "-movflags","+faststart","-y", prev_out
        ], timeout=900)
        if (code != 0) or (not os.path.exists(prev_out)):
            raise RuntimeError(f"Preview watermark failed: {err[:500]}")

    # Final 1080p
    if want_final:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", dur, "-i", source_path,
            "-c:v","libx264","-preset","faster","-crf","20",
            "-c:a","aac","-b:a","192k",
            *compose_vf(scale_filter(1080), drawtext_expr(watermark_text) if watermark_text else None),
            "-movflags","+faststart","-y", final_out
        ], timeout=1800)
        if (code != 0) or (not os.path.exists(final_out)):
            raise RuntimeError(f"Final export failed: {err[:500]}")

    result = {
        "preview_url": f"/media/previews/{os.path.basename(prev_out)}" if want_preview else None,
        "final_url":   f"/media/exports/{os.path.basename(final_out)}"  if want_final  else None,
        "start": start,
        "end": end
    }
    if want_preview and os.path.exists(prev_out):
        result["preview_seconds"] = ffprobe_duration(prev_out)
        result["preview_bytes"]   = file_size(prev_out)
    if want_final and os.path.exists(final_out):
        result["final_seconds"] = ffprobe_duration(final_out)
        result["final_bytes"]   = file_size(final_out)
    return result

# =========================
# Routes — Clips
# =========================
@app.post("/clip_preview")
async def clip_preview(
    request: Request,
    file: UploadFile = File(None),
    url: str = Form(None),
    start: str = Form(...),
    end: str   = Form(...),
    watermark: str = Form("0"),
    wm_text: str   = Form("@ClipForge"),
    final_1080: str = Form("0"),
):
    try:
        if file is not None:
            src = os.path.join(UPLOAD_DIR, safe(file.filename))
            with open(src, "wb") as f:
                shutil.copyfileobj(file.file, f)
        elif url:
            tmp = download_to_tmp(url)
            src = os.path.join(UPLOAD_DIR, safe(os.path.basename(url) or f"remote_{nowstamp()}.mp4"))
            shutil.copy(tmp, src)
            os.remove(tmp)
        else:
            return JSONResponse({"ok": False, "error": "Provide file or url."}, 400)

        out = await build_clip(
            src, start.strip(), end.strip(),
            want_preview=True,
            want_final=(final_1080 == "1"),
            watermark_text=(wm_text if watermark == "1" else None),
        )
        # return absolute URLs for frontend convenience
        out["preview_url"] = abs_url(request, out.get("preview_url"))
        out["final_url"]   = abs_url(request, out.get("final_url"))
        return JSONResponse({"ok": True, **out})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

# Back-compat: returns the preview MP4 blob
@app.post("/clip")
async def clip_endpoint(
    file: UploadFile = File(...),
    start: str = Form(...),
    end: str   = Form(...),
    watermark: str = Form("0"),
    wm_text: str   = Form("@ClipForge"),
):
    try:
        src = os.path.join(UPLOAD_DIR, safe(file.filename))
        with open(src, "wb") as f:
            shutil.copyfileobj(file.file, f)

        result = await build_clip(
            src, start.strip(), end.strip(),
            want_preview=True, want_final=False,
            watermark_text=(wm_text if watermark == "1" else None),
        )
        if not result.get("preview_url"):
            return JSONResponse({"ok": False, "error": "No preview generated."}, 500)

        preview_file = os.path.join(PREVIEW_DIR, os.path.basename(result["preview_url"]))
        return FileResponse(preview_file, filename=os.path.basename(preview_file), media_type="video/mp4")
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

@app.post("/clip_multi")
async def clip_multi(
    request: Request,
    file: UploadFile = File(None),
    url: str = Form(None),
    sections: str = Form(...),  # [{"start":"..","end":".."}]
    watermark: str = Form("0"),
    wm_text: str   = Form("@ClipForge"),
    preview_480: str = Form("1"),
    final_1080: str  = Form("0"),
):
    tmp = None
    try:
        # Resolve source
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
                    "preview_url": abs_url(request, r.get("preview_url")),
                    "final_url":   abs_url(request, r.get("final_url")),
                    "start": s, "end": e
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
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

# =========================
# Transcribe (URL or File) + Supabase save (resilient)
# =========================
@app.post("/transcribe")
async def transcribe_audio(
    url: str = Form(None),
    file: UploadFile = File(None),
    user_email: str = Form("guest@clipforge.app"),
):
    tmp_path = None
    audio_mp3 = None
    try:
        # 1) Resolve input to local file
        if file:
            suffix = os.path.splitext(file.filename)[1] or ".webm"
            tmp_path = os.path.join(TMP_DIR, f"upl_{nowstamp()}{suffix}")
            with open(tmp_path, "wb") as f:
                f.write(await file.read())
        elif url:
            # Prefer direct audio extract to mp3 if possible
            base = os.path.join(TMP_DIR, f"audio_{nowstamp()}")
            code, err = run([
                "yt-dlp",
                "--no-playlist",
                "-x", "--audio-format", "mp3", "--audio-quality", "192K",
                "-o", base + ".%(ext)s",
                "--force-overwrites",
                url
            ], timeout=900)
            mp3_candidate = base + ".mp3"
            if code == 0 and os.path.exists(mp3_candidate):
                audio_mp3 = mp3_candidate
            else:
                # Fallback: fetch video then convert to mp3
                tmp_path = download_to_tmp(url)
        else:
            return JSONResponse({"ok": False, "error": "No file or URL provided."}, 400)

        # 2) Convert to mp3 if needed
        if not audio_mp3:
            audio_mp3 = (tmp_path.rsplit(".",1)[0] + ".mp3") if tmp_path else os.path.join(TMP_DIR, f"audio_{nowstamp()}.mp3")
            code, err = run(["ffmpeg","-y","-i",tmp_path,"-vn","-acodec","libmp3lame","-b:a","192k",audio_mp3], timeout=900)
            if code != 0 or not os.path.exists(audio_mp3):
                return JSONResponse({"ok": False, "error": f"FFmpeg audio convert failed: {err}."}, 500)

        # 3) Whisper
        with open(audio_mp3, "rb") as a:
            tr = client.audio.transcriptions.create(model="whisper-1", file=a, response_format="text")
        text_output = tr.strip() if isinstance(tr, str) else str(tr) or "(no text)"

        # 4) Supabase save (best effort, resilient to schema mismatch)
        if supabase:
            try:
                # Try primary column name first
                payload = {
                    "user_email": user_email,
                    SUPABASE_TEXT_COL_PRIMARY: text_output,
                    "created_at": datetime.utcnow().isoformat()
                }
                res = supabase.table(SUPABASE_TABLE).insert(payload).execute()
                # If API complains about missing column, retry using ALT column name
                if getattr(res, "data", None) is None and getattr(res, "error", None):
                    raise Exception(res.error)
            except Exception as e1:
                try:
                    payload_alt = {
                        "user_email": user_email,
                        SUPABASE_TEXT_COL_ALT: text_output,
                        "created_at": datetime.utcnow().isoformat()
                    }
                    supabase.table(SUPABASE_TABLE).insert(payload_alt).execute()
                except Exception as e2:
                    print("⚠️ Supabase insert failed (both columns). Skipping. Errors:", e1, " / ", e2)

        return JSONResponse({"ok": True, "text": text_output})
    except Exception as e:
        print("❌ /transcribe error:", e)
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    finally:
        # Cleanup temp files (best effort)
        for p in (tmp_path, audio_mp3):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

# =========================
# AI helper
# =========================
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
        msgs = [{"role":"system","content":SYSTEM_PROMPT}]
        if transcript:
            msgs.append({"role":"system","content":f"Transcript:\n{transcript[:12000]}"} )
        try:
            prev = json.loads(history)
            if isinstance(prev, list): msgs += prev
        except Exception:
            pass
        msgs.append({"role":"user","content":user_message})

        resp = client.chat.completions.create(model="gpt-4o-mini", temperature=0.3, messages=msgs)
        out = resp.choices[0].message.content.strip()
        return JSONResponse({"ok": True, "reply": out})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

@app.post("/auto_clip")
async def auto_clip(transcript: str = Form(...), max_clips: int = Form(3)):
    try:
        prompt = (
            "From this transcript, pick up to {k} high-impact short moments (10–45s). "
            "Return strict JSON with key 'clips' = list of {{start,end,summary}}.\n\nTranscript:\n{t}"
        ).format(k=max_clips, t=transcript[:12000])
        resp = client.chat.completions.create(
            model="gpt-4o-mini", temperature=0.2,
            messages=[{"role":"user","content":prompt}]
        )
        raw = resp.choices[0].message.content
        try:
            data = json.loads(raw)
        except Exception:
            s,e = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[s:e+1]) if s!=-1 and e!=-1 else {"clips":[]}

        clips = []
        for c in (data.get("clips") or [])[:max_clips]:
            clips.append({
                "start": str(c.get("start","00:00:00")).strip(),
                "end":   str(c.get("end","00:00:10")).strip(),
                "summary": str(c.get("summary","")).strip()[:140]
            })
        return JSONResponse({"ok": True, "clips": clips})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
