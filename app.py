# app.py — ClipForge AI Backend v3 (clean + robust URL/file transcription, fast clips, optional Supabase)
import os, json, shutil, asyncio, subprocess, glob, tempfile
from datetime import datetime
from typing import Optional, List, Tuple
from zipfile import ZipFile

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from openai import OpenAI
client = OpenAI()  # requires OPENAI_API_KEY

# ---------- Optional Supabase ----------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "transcriptions")
SUPABASE_TEXT_COLUMN = os.getenv("SUPABASE_TEXT_COLUMN", "text")
SUPABASE_EMAIL_COLUMN = os.getenv("SUPABASE_EMAIL_COLUMN", "user_email")
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print("⚠️ Supabase SDK not available:", e)

# ---------- Paths & App ----------
APP_NAME = "ClipForge AI Backend v3"
BASE_DIR = "/data"
UPLOAD_DIR  = os.path.join(BASE_DIR, "uploads")
PREVIEW_DIR = os.path.join(BASE_DIR, "previews")
EXPORT_DIR  = os.path.join(BASE_DIR, "exports")
TMP_DIR     = "/tmp"
for d in (UPLOAD_DIR, PREVIEW_DIR, EXPORT_DIR, TMP_DIR):
    os.makedirs(d, exist_ok=True)

app = FastAPI(title=APP_NAME)

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

app.mount("/media/previews", StaticFiles(directory=PREVIEW_DIR), name="previews")
app.mount("/media/exports", StaticFiles(directory=EXPORT_DIR), name="exports")

# Absolute URL base (optional override)
PUBLIC_BASE = os.getenv("PUBLIC_BASE", "").rstrip("/")

# ---------- Helpers ----------
def nowstamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")

def safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in ("-", "_", "."))[:120]

def run(cmd: List[str], timeout=1200) -> Tuple[int, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    # combine for easier debugging
    return p.returncode, (p.stdout + "\n" + p.stderr).strip()

def compose_vf(scale: Optional[str], drawtext: Optional[str]) -> List[str]:
    if scale and drawtext:
        return ["-vf", f"{scale},drawtext={drawtext}"]
    if scale:
        return ["-vf", scale]
    if drawtext:
        return ["-vf", f"drawtext={drawtext}"]
    return []

def scale_filter(h: int) -> str:
    return f"scale=-2:{h}:flags=lanczos"

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
        if code == 0:
            val = (out or "").strip().splitlines()[-1]
            return float(val) if val else None
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
    if PUBLIC_BASE:
        return f"{PUBLIC_BASE}{path}"
    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"

def make_names(original_name: str, start: str, end: str) -> Tuple[str, str]:
    base = safe(os.path.splitext(original_name)[0] or "clip")
    stamp = nowstamp()
    prev_name  = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_prev_{stamp}.mp4"
    final_name = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_1080_{stamp}.mp4"
    return os.path.join(PREVIEW_DIR, prev_name), os.path.join(EXPORT_DIR, final_name)

def download_to_tmp(url: str) -> str:
    """
    Robust remote fetch: use yt-dlp for platforms; else direct HTTP stream.
    Always returns a local video path (mp4/webm/m4a etc).
    """
    tmp_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    u = (url or "").lower()
    if any(k in u for k in ["youtube", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com"]):
        code, err = run(["yt-dlp", "-f", "mp4", "-o", tmp_path, url], timeout=600)
        if code != 0 or not os.path.exists(tmp_path):
            raise RuntimeError(f"yt-dlp failed to fetch URL.\n{err}")
    else:
        # lightweight fallback
        import requests
        r = requests.get(url, stream=True, timeout=90)
        if r.status_code != 200:
            raise RuntimeError(f"Failed to download: HTTP {r.status_code}")
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
    return tmp_path

# ---------- Clip core ----------
async def build_clip(
    source_path: str,
    start: str,
    end: str,
    want_preview: bool,
    want_final: bool,
    watermark_text: Optional[str],
):
    prev_out, final_out = make_names(os.path.basename(source_path), start, end)

    # Fast preview (stream copy when possible)
    if want_preview and not watermark_text:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-to", end, "-i", source_path,
            "-c","copy","-movflags","+faststart","-y", prev_out
        ], timeout=300)
        if (code != 0) or (not os.path.exists(prev_out)):
            # fallback to quick encode
            code, err = run([
                "ffmpeg","-hide_banner","-loglevel","error",
                "-ss", start, "-to", end, "-i", source_path,
                "-c:v","libx264","-preset","veryfast","-crf","28",
                "-c:a","aac","-b:a","128k",
                "-movflags","+faststart","-y", prev_out
            ], timeout=900)
            if (code != 0) or (not os.path.exists(prev_out)):
                raise RuntimeError(f"Preview failed:\n{err}")

    elif want_preview and watermark_text:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-to", end, "-i", source_path,
            "-c:v","libx264","-preset","veryfast","-crf","26",
            "-c:a","aac","-b:a","128k",
            *compose_vf(scale_filter(480), drawtext_expr(watermark_text)),
            "-movflags","+faststart","-y", prev_out
        ], timeout=1200)
        if (code != 0) or (not os.path.exists(prev_out)):
            raise RuntimeError(f"Preview watermark failed:\n{err}")

    if want_final:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-to", end, "-i", source_path,
            "-c:v","libx264","-preset","faster","-crf","20",
            "-c:a","aac","-b:a","192k",
            *compose_vf(scale_filter(1080), drawtext_expr(watermark_text) if watermark_text else None),
            "-movflags","+faststart","-y", final_out
        ], timeout=1800)
        if (code != 0) or (not os.path.exists(final_out)):
            raise RuntimeError(f"Final export failed:\n{err}")

    result = {
        "preview_url": f"/media/previews/{os.path.basename(prev_out)}" if want_preview else None,
        "final_url":   f"/media/exports/{os.path.basename(final_out)}"  if want_final  else None,
        "preview_seconds": ffprobe_duration(prev_out) if want_preview and os.path.exists(prev_out) else None,
        "preview_bytes": file_size(prev_out) if want_preview and os.path.exists(prev_out) else None,
        "final_seconds": ffprobe_duration(final_out) if want_final and os.path.exists(final_out) else None,
        "final_bytes": file_size(final_out) if want_final and os.path.exists(final_out) else None,
        "start": start,
        "end": end
    }
    return result

# ---------- Routes ----------
@app.get("/")
def health():
    return {"ok": True, "service": APP_NAME}

# Clips: JSON (absolute URLs for frontend)
@app.post("/clip")
async def clip_json(
    request: Request,
    file: UploadFile = File(None),
    url: str = Form(None),
    start: str = Form(...),
    end: str = Form(...),
    preview_480: str = Form("1"),
    final_1080: str  = Form("0"),
    watermark: str   = Form("0"),
    wm_text: str     = Form("@ClipForge"),
):
    tmp = None
    try:
        start, end = start.strip(), end.strip()
        if not start or not end:
            return JSONResponse({"ok": False, "error": "Start and end are required."}, 400)

        if file is not None:
            src = os.path.join(UPLOAD_DIR, safe(file.filename or f"upload_{nowstamp()}.mp4"))
            with open(src, "wb") as f:
                shutil.copyfileobj(file.file, f)
        elif url:
            tmp = download_to_tmp(url)
            safe_name = safe(os.path.basename(url) or f"remote_{nowstamp()}.mp4")
            src = os.path.join(UPLOAD_DIR, safe_name)
            shutil.copy(tmp, src)
        else:
            return JSONResponse({"ok": False, "error": "Provide a file or a url."}, 400)

        out = await build_clip(
            src, start, end,
            want_preview=(preview_480 == "1"),
            want_final=(final_1080 == "1"),
            watermark_text=(wm_text if watermark == "1" else None),
        )
        out["preview_url"] = abs_url(request, out.get("preview_url"))
        out["final_url"]   = abs_url(request, out.get("final_url"))
        return JSONResponse({"ok": True, **out})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    finally:
        try:
            if tmp and os.path.exists(tmp): os.remove(tmp)
        except: pass

# Clips: ZIP or blob compatibility if you need it later
@app.post("/clip_blob")
async def clip_blob(
    file: UploadFile = File(...),
    start: str = Form(...),
    end: str   = Form(...),
    watermark: str   = Form("0"),
    wm_text: str     = Form("@ClipForge"),
):
    try:
        src = os.path.join(UPLOAD_DIR, safe(file.filename))
        with open(src, "wb") as f:
            shutil.copyfileobj(file.file, f)
        out = await build_clip(
            src, start.strip(), end.strip(),
            want_preview=True, want_final=False,
            watermark_text=(wm_text if watermark == "1" else None)
        )
        if not out.get("preview_url"):
            return JSONResponse({"ok": False, "error": "No preview generated."}, 500)
        preview_file = os.path.join(PREVIEW_DIR, os.path.basename(out["preview_url"]))
        return FileResponse(preview_file, filename=os.path.basename(preview_file), media_type="video/mp4")
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

@app.post("/clip_multi")
async def clip_multi(
    request: Request,
    file: UploadFile = File(None),
    url: str = Form(None),
    sections: str = Form(...),  # [{"start":"..","end":".."}]
    preview_480: str = Form("1"),
    final_1080: str  = Form("0"),
    watermark: str   = Form("0"),
    wm_text: str     = Form("@ClipForge"),
):
    tmp = None
    try:
        if file is not None:
            src = os.path.join(UPLOAD_DIR, safe(file.filename or f"upload_{nowstamp()}.mp4"))
            with open(src, "wb") as f:
                f.write(await file.read())
        elif url:
            tmp = download_to_tmp(url)
            safe_name = safe(os.path.basename(url) or f"remote_{nowstamp()}.mp4")
            src = os.path.join(UPLOAD_DIR, safe_name)
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
        want_prev, want_final = (preview_480 == "1"), (final_1080 == "1")

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
                        fp = os.path.join(EXPORT_DIR, os.path.basename(r["final_url"]))
                        if os.path.exists(fp):
                            z.write(fp, arcname=os.path.basename(fp))
            zip_url = abs_url(request, f"/media/exports/{zip_name}")

        return JSONResponse({"ok": True, "items": results, "zip_url": zip_url})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    finally:
        try:
            if tmp and os.path.exists(tmp): os.remove(tmp)
        except: pass

# ---------- Transcribe (file OR URL) ----------
# Robust: URL -> try direct audio MP3 via yt-dlp, else download video then ffmpeg → mp3
@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(None),
    url: str = Form(None),
    user_email: str = Form("guest@clipforge.app")
):
    base_marker = f"audio_{nowstamp()}"
    candidate_base = os.path.join(TMP_DIR, base_marker)

    def find_mp3s() -> List[str]:
        return glob.glob(os.path.join(TMP_DIR, f"{base_marker}*.mp3"))

    try:
        mp3_path = None

        if url:
            code, err = run([
                "yt-dlp", "--no-playlist",
                "-x", "--audio-format", "mp3", "--audio-quality", "192K",
                "-o", candidate_base + ".%(ext)s",
                "--force-overwrites", url
            ], timeout=900)
            cands = find_mp3s()
            if code == 0 and cands:
                mp3_path = sorted(cands, key=lambda p: os.path.getmtime(p))[-1]
            else:
                # fallback: fetch mp4 then convert
                src = candidate_base + ".mp4"
                code2, err2 = run([
                    "yt-dlp","--no-playlist","-f","mp4","-o",src,"--force-overwrites", url
                ], timeout=900)
                if code2 != 0 or not os.path.exists(src):
                    return JSONResponse({"ok": False, "error": f"Failed to fetch URL.\n{err or err2}"}, 400)
                mp3_path = candidate_base + ".mp3"
                code3, err3 = run(["ffmpeg","-y","-i",src,"-vn","-acodec","libmp3lame","-b:a","192k", mp3_path], timeout=900)
                if code3 != 0 or not os.path.exists(mp3_path):
                    return JSONResponse({"ok": False, "error": f"FFmpeg convert failed:\n{err3}"}, 500)

        elif file is not None:
            src = os.path.join(TMP_DIR, f"upl_{nowstamp()}_{safe(file.filename or 'audio')}")
            with open(src, "wb") as f:
                shutil.copyfileobj(file.file, f)
            mp3_path = src.rsplit(".",1)[0] + ".mp3"
            code, err = run(["ffmpeg","-y","-i",src,"-vn","-acodec","libmp3lame","-b:a","192k", mp3_path], timeout=900)
            if code != 0 or not os.path.exists(mp3_path):
                return JSONResponse({"ok": False, "error": f"FFmpeg audio convert failed:\n{err}"}, 500)
        else:
            return JSONResponse({"ok": False, "error":"No file or URL provided."}, 400)

        # Whisper transcription
        with open(mp3_path, "rb") as a:
            tr = client.audio.transcriptions.create(model="whisper-1", file=a, response_format="text")
        text = tr.strip() if isinstance(tr, str) else str(tr)
        if not text:
            text = "(no text)"

        # Optional Supabase save (never blocks success)
        if supabase:
            try:
                supabase.table(SUPABASE_TABLE).insert({
                    SUPABASE_EMAIL_COLUMN: user_email,
                    SUPABASE_TEXT_COLUMN: text,
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                print("⚠️ Supabase insert error:", e)

        return JSONResponse({"ok": True, "text": text})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    finally:
        # clean older temp media (2h+)
        try:
            for path in glob.glob(os.path.join(TMP_DIR, "*")):
                try:
                    if os.path.isfile(path) and (path.endswith(".mp3") or path.endswith(".mp4") or path.endswith(".webm") or path.endswith(".m4a")):
                        if (datetime.utcnow().timestamp() - os.path.getmtime(path)) > 2*3600:
                            os.remove(path)
                except Exception:
                    pass
        except Exception:
            pass

# ---------- AI helper ----------
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
        except: pass
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
        resp = client.chat.completions.create(model="gpt-4o-mini", temperature=0.2,
                                              messages=[{"role":"user","content":prompt}])
        raw = resp.choices[0].message.content
        try:
            data = json.loads(raw)
        except:
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
