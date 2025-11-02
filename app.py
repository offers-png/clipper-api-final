# app.py
import os, json, shutil, asyncio, subprocess
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from zipfile import ZipFile

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media/previews", StaticFiles(directory=PREVIEW_DIR), name="previews")
app.mount("/media/exports", StaticFiles(directory=EXPORT_DIR), name="exports")

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
    # ffmpeg -t accepts seconds with decimals
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

    # ---- Fast preview path (stream-copy) when NO watermark ----
    if want_preview and not watermark_text:
        # cut quickly; put moov atom at front for instant playback
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", dur, "-i", source_path,
            "-c","copy","-movflags","+faststart","-y", prev_out
        ], timeout=300)
        # Some codecs/containers cannot be copied cleanly; fallback to fast encode
        if (code != 0) or (not os.path.exists(prev_out)):
            code, err = run([
                "ffmpeg","-hide_banner","-loglevel","error",
                "-ss", start, "-t", dur, "-i", source_path,
                "-c:v","libx264","-preset","veryfast","-crf","28",
                "-c:a","aac","-b:a","128k",
                *compose_vf(None, None),
                "-movflags","+faststart","-y", prev_out
            ], timeout=600)
            if (code != 0) or (not os.path.exists(prev_out)):
                raise RuntimeError(f"Preview failed: {err}")

    # ---- Preview path with watermark (needs encode) ----
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

    # ---- Optional high-quality export ----
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

# ---------- Routes ----------
@app.get("/")
def health(): return {"ok": True, "service": APP_NAME}

@app.post("/clip")
async def clip_endpoint(
    file: UploadFile = File(...),
    start: str = Form(...),
    end: str   = Form(...),
    preview_480: str = Form("1"),
    final_1080: str  = Form("0"),
    watermark: str   = Form("0"),
    wm_text: str     = Form("@ClipForge"),
):
    try:
        src = os.path.join(UPLOAD_DIR, safe(file.filename))
        with open(src, "wb") as f: shutil.copyfileobj(file.file, f)

        out = await build_clip(
            src, start.strip(), end.strip(),
            want_preview=(preview_480 == "1"),
            want_final=(final_1080 == "1"),
            watermark_text=(wm_text if watermark == "1" else None),
        )
        return JSONResponse({ "ok": True, **out })
    except Exception as e:
        return JSONResponse({ "ok": False, "error": str(e) }, status_code=500)

@app.post("/clip_multi")
async def clip_multi_endpoint(
    file: UploadFile = File(...),
    sections: str   = Form(...),  # [{"start":"..","end":".."}]
    preview_480: str = Form("1"),
    final_1080: str  = Form("0"),
    watermark: str   = Form("0"),
    wm_text: str     = Form("@ClipForge"),
):
    try:
        src = os.path.join(UPLOAD_DIR, safe(file.filename))
        with open(src, "wb") as f: f.write(await file.read())

        try:
            segs = json.loads(sections)
            assert isinstance(segs, list) and segs
        except Exception:
            return JSONResponse({"ok": False, "error": "sections must be a non-empty JSON list"}, 400)

        want_preview = (preview_480 == "1")
        want_final   = (final_1080 == "1")
        wm           = (wm_text if watermark == "1" else None)

        sem = asyncio.Semaphore(3)
        async def worker(s,e):
            async with sem:
                return await build_clip(src, s.strip(), e.strip(), want_preview, want_final, wm)

        tasks = [worker(s.get("start","00:00:00"), s.get("end","00:00:10")) for s in segs]
        results = await asyncio.gather(*tasks)

        # Optional ZIP only if final videos were built
        zip_url = None
        if want_final:
            zip_name = f"clips_{nowstamp()}.zip"
            zip_path = os.path.join(EXPORT_DIR, zip_name)
            with ZipFile(zip_path, "w") as z:
                for r in results:
                    if r.get("final_url"):
                        fp = os.path.join(EXPORT_DIR, os.path.basename(r["final_url"]))
                        if os.path.exists(fp): z.write(fp, arcname=os.path.basename(fp))
            zip_url = f"/media/exports/{zip_name}"

        return JSONResponse({"ok": True, "items": results, "zip_url": zip_url})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
