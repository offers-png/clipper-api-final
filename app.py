# app.py — ClipForge AI Backend v3 (V1)
# Changes:
#  - /transcribe accepts URL or file; uses yt-dlp -> mp3 -> Whisper
#  - Response format: {"ok": True, "text": "..."} on success
#  - CORS allows your two frontends and localhost

import os, json, shutil, asyncio, subprocess
from datetime import datetime
from typing import Optional, List, Tuple
from zipfile import ZipFile

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from openai import OpenAI
client = OpenAI()  # requires OPENAI_API_KEY

APP_NAME = "ClipForge AI Backend v3"
BASE_DIR   = "/data"
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PREVIEW_DIR= os.path.join(BASE_DIR, "previews")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
TMP_DIR    = "/tmp"

for d in (UPLOAD_DIR, PREVIEW_DIR, EXPORT_DIR, TMP_DIR):
    os.makedirs(d, exist_ok=True)

app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://clipper-frontend.onrender.com",
        "https://ptsel-frontend.onrender.com",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media/previews", StaticFiles(directory=PREVIEW_DIR), name="previews")
app.mount("/media/exports",  StaticFiles(directory=EXPORT_DIR),  name="exports")

def nowstamp():
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")

def safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in ("-", "_", "."))[:120]

def run(cmd: List[str], timeout=1200) -> Tuple[int, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return p.returncode, p.stderr

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

async def build_clip(
    source_path: str,
    start: str,
    end: str,
    want_preview: bool,
    want_final: bool,
    watermark_text: Optional[str],
):
    base = safe(os.path.splitext(os.path.basename(source_path))[0])
    stamp = nowstamp()
    dur = duration_from(start, end)

    prev_name  = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_prev_{stamp}.mp4"
    final_name = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_1080_{stamp}.mp4"
    prev_out   = os.path.join(PREVIEW_DIR, prev_name)
    final_out  = os.path.join(EXPORT_DIR, final_name)

    # Fast preview when no watermark (stream copy)
    if want_preview and not watermark_text:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", dur, "-i", source_path,
            "-c","copy","-movflags","+faststart","-y", prev_out
        ], timeout=300)
        if (code != 0) or (not os.path.exists(prev_out)):
            # Fallback encode
            code, err = run([
                "ffmpeg","-hide_banner","-loglevel","error",
                "-ss", start, "-t", dur, "-i", source_path,
                "-c:v","libx264","-preset","veryfast","-crf","28",
                "-c:a","aac","-b:a","128k",
                "-movflags","+faststart","-y", prev_out
            ], timeout=600)
            if (code != 0) or (not os.path.exists(prev_out)):
                raise RuntimeError(f"Preview failed: {err}")

    # Preview with watermark (encode)
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
            raise RuntimeError(f"Preview watermark failed: {err}")

    # Optional final export
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
            raise RuntimeError(f"Final export failed: {err}")

    return {
        "preview_url": f"/media/previews/{os.path.basename(prev_out)}" if want_preview else None,
        "final_url":   f"/media/exports/{os.path.basename(final_out)}"  if want_final  else None,
        "start": start, "end": end
    }

@app.get("/")
def health():
    return {"ok": True, "service": APP_NAME}

# ---------- CLIP ----------
@app.post("/clip")
async def clip_endpoint(
    file: UploadFile = File(...),
    start: str = Form(...),
    end:   str = Form(...),
    preview_480: str = Form("1"),
    final_1080:  str = Form("0"),
    watermark:   str = Form("0"),
    wm_text:     str = Form("@ClipForge"),
):
    try:
        src = os.path.join(UPLOAD_DIR, safe(file.filename))
        with open(src, "wb") as f:
            shutil.copyfileobj(file.file, f)

        out = await build_clip(
            src, start.strip(), end.strip(),
            want_preview=(preview_480 == "1"),
            want_final=(final_1080 == "1"),
            watermark_text=(wm_text if watermark == "1" else None),
        )
        return JSONResponse({"ok": True, **out})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.post("/clip_multi")
async def clip_multi_endpoint(
    file: UploadFile = File(...),
    sections: str   = Form(...),  # [{"start":"..","end":".."}]
    preview_480: str = Form("1"),
    final_1080:  str = Form("0"),
    watermark:   str = Form("0"),
    wm_text:     str = Form("@ClipForge"),
):
    try:
        src = os.path.join(UPLOAD_DIR, safe(file.filename))
        with open(src, "wb") as f:
            f.write(await file.read())

        try:
            segs = json.loads(sections)
            assert isinstance(segs, list) and segs
        except Exception:
            return JSONResponse({"ok": False, "error": "sections must be a non-empty JSON list"}, 400)

        want_preview = (preview_480 == "1")
        want_final   = (final_1080  == "1")
        wm           = (wm_text if watermark == "1" else None)

        sem = asyncio.Semaphore(3)
        async def worker(s,e):
            async with sem:
                return await build_clip(src, s.strip(), e.strip(), want_preview, want_final, wm)

        tasks = [worker(s.get("start","00:00:00"), s.get("end","00:00:10")) for s in segs]
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
            zip_url = f"/media/exports/{zip_name}"

        return JSONResponse({"ok": True, "items": results, "zip_url": zip_url})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

# ---------- TRANSCRIBE (file OR url) ----------
@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(None),
    url: str = Form(None),
):
    src = None
    mp3 = None
    try:
        if not (file or url):
            return JSONResponse({"ok": False, "error": "No file or URL provided."}, 400)

        if file:
            src = os.path.join(TMP_DIR, f"upl_{nowstamp()}_{safe(file.filename)}")
            with open(src, "wb") as f:
                shutil.copyfileobj(file.file, f)
        else:
            src = os.path.join(TMP_DIR, f"remote_{nowstamp()}.mp4")
            # Best effort mp4. If needed, try 'bestaudio[ext=m4a]/bestaudio/best'
            code, err = run(["yt-dlp", "-f", "mp4", "-o", src, url], timeout=300)
            if code != 0 or (not os.path.exists(src)):
                return JSONResponse({"ok": False, "error": "Failed to fetch URL."}, 400)

        mp3 = src.rsplit(".", 1)[0] + ".mp3"
        code, err = run(["ffmpeg", "-y", "-i", src, "-vn", "-acodec", "libmp3lame", "-b:a", "192k", mp3], timeout=600)
        if code != 0 or (not os.path.exists(mp3)):
            return JSONResponse({"ok": False, "error": f"FFmpeg convert failed: {err}"}, 500)

        with open(mp3, "rb") as a:
            tr = client.audio.transcriptions.create(model="whisper-1", file=a, response_format="text")
        text = tr.strip() if isinstance(tr, str) else str(tr)

        return JSONResponse({"ok": True, "text": text or "(no text)"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    finally:
        for p in (src, mp3):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except:
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
        resp = client.chat.completions.create(
            model="gpt-4o-mini", temperature=0.2,
            messages=[{"role":"user","content":prompt}]
        )
        raw = resp.choices[0].message.content
        try:
            data = json.loads(raw)
        except:
            s,e = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[s:e+1]) if s!=-1 and e!=-1 else {"clips":[]}

        clips = []
        for c in (data.get("clips") or [])[:max_clips]:
            clips.append({
                "start":   str(c.get("start","00:00:00")).strip(),
                "end":     str(c.get("end","00:00:10")).strip(),
                "summary": str(c.get("summary","")).strip()[:140]
            })
        return JSONResponse({"ok": True, "clips": clips})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
