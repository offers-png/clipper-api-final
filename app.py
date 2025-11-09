# app.py — ClipForge AI (Enterprise)
# - Auth (Supabase) + Credits/billing hooks
# - URL+file transcription (Whisper)
# - Single-clip preview + optional 1080p final
# - Multi-clip + ZIP
# - Per-clip transcript (optional) + duration
# - Absolute URLs for frontend; static hosting for outputs
# - Health HEAD "/" (Render friendly)
# - Background cleanup worker

import os, json, shutil, asyncio
from datetime import datetime
from typing import Optional, List
from zipfile import ZipFile

from fastapi import FastAPI, UploadFile, File, Form, Request, Depends
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from openai import OpenAI

from models import ClipRequest, MultiClipRequest
from utils import (
    ensure_dirs, abs_url, safe, run_ffmpeg_preview, run_ffmpeg_final,
    ffprobe_duration, file_size, download_to_tmp, TMP_DIR, UPLOAD_DIR,
    PREVIEW_DIR, EXPORT_DIR, PUBLIC_BASE_FROM, add_watermark_drawtext
)
from db import init_supabase, upsert_video_row, insert_clip_row
from billing import require_seconds, charge_seconds
from auth import require_user
from workers import start_cleanup_task

APP_TITLE = "ClipForge AI Backend (Enterprise)"
APP_VERSION = "4.0.0"

app = FastAPI(title=APP_TITLE, version=APP_VERSION)

# -------- Env / Clients --------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI() if OPENAI_API_KEY else None
supabase = init_supabase()

# -------- CORS --------
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

# -------- Static hosting for outputs --------
ensure_dirs()
app.mount("/media/previews", StaticFiles(directory=PREVIEW_DIR), name="previews")
app.mount("/media/exports",  StaticFiles(directory=EXPORT_DIR),  name="exports")

# -------- Health --------
@app.get("/")
def health_get():
    return {"ok": True, "service": APP_TITLE, "version": APP_VERSION}

@app.head("/")
def health_head():
    return Response(status_code=200)

@app.get("/api/health")
def api_health():
    return {"ok": True}

# -------- Startup: background cleanup --------
@app.on_event("startup")
async def _on_start():
    asyncio.create_task(start_cleanup_task())

# =========================================================
# Helpers
# =========================================================
async def make_clip_bundle(
    request: Request,
    src_path: str,
    start: str,
    end: str,
    want_preview: bool,
    want_final: bool,
    watermark_text: Optional[str],
    transcribe_clip: bool,
    user_id: Optional[str],
    user_email: Optional[str],
):
    """Build one clip; optionally run transcript; record to DB; return dict with links + metadata."""
    base = os.path.splitext(os.path.basename(src_path))[0]
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    prev_name  = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_prev_{stamp}.mp4"
    final_name = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_1080_{stamp}.mp4"

    preview_path = os.path.join(PREVIEW_DIR, prev_name) if want_preview else None
    final_path   = os.path.join(EXPORT_DIR,  final_name) if want_final else None

    draw = add_watermark_drawtext(watermark_text) if watermark_text else None

    # Build preview
    if want_preview:
        ok, err = await run_ffmpeg_preview(src_path, start, end, preview_path, draw)
        if not ok or not os.path.exists(preview_path):
            raise RuntimeError(f"Preview failed: {err[:500]}")

    # Build final
    if want_final:
        ok, err = await run_ffmpeg_final(src_path, start, end, final_path, draw)
        if not ok or not os.path.exists(final_path):
            raise RuntimeError(f"Final failed: {err[:500]}")

    preview_url = abs_url(request, f"/media/previews/{os.path.basename(preview_path)}") if preview_path else None
    final_url   = abs_url(request, f"/media/exports/{os.path.basename(final_path)}")   if final_path   else None

    # Per-clip transcript (optional)
    clip_transcript = None
    if transcribe_clip and client:
        try:
            # choose the smaller file to transcribe (preview preferred if exists)
            chosen = preview_path or final_path
            if chosen:
                # Extract audio to mp3 for Whisper
                from utils import to_mp3_for_whisper
                mp3_path = await to_mp3_for_whisper(chosen)
                with open(mp3_path, "rb") as a:
                    t = client.audio.transcriptions.create(
                        model="whisper-1", file=a, response_format="text"
                    )
                clip_transcript = t.strip() if isinstance(t, str) else str(t)
                try:
                    os.remove(mp3_path)
                except Exception:
                    pass
        except Exception as e:
            clip_transcript = None

    # DB rows (best-effort; skip on failure)
    try:
        video_row = upsert_video_row(
            user_id=user_id,
            src_url=f"file://{safe(base)}",
            duration_sec=None,
            transcript=None
        )
        video_id = None
        if video_row and getattr(video_row, "data", None):
            rec = video_row.data[0]
            video_id = rec.get("id") if isinstance(rec, dict) else None

        insert_clip_row(
            user_id=user_id,
            video_id=video_id,
            start_sec=None, end_sec=None,
            preview_url=preview_url, final_url=final_url,
            transcript=clip_transcript
        )
    except Exception:
        pass

    # Metadata
    result = {
        "start": start, "end": end,
        "preview_url": preview_url,
        "final_url": final_url,
        "preview_seconds": ffprobe_duration(preview_path) if preview_path else None,
        "final_seconds":   ffprobe_duration(final_path)   if final_path   else None,
        "preview_bytes":   file_size(preview_path)         if preview_path else None,
        "final_bytes":     file_size(final_path)           if final_path   else None,
        "transcript": clip_transcript,
    }
    return result

# =========================================================
# Routes — Clips
# =========================================================

@app.post("/clip_preview")
async def clip_preview(
    request: Request,
    start: str = Form(...),
    end: str   = Form(...),
    watermark: str = Form("0"),
    wm_text: str   = Form("@ClipForge"),
    final_1080: str = Form("0"),
    include_transcript: str = Form("0"),
    file: UploadFile = File(None),
    url: str = Form(None),
    user = Depends(require_user),
    _ = Depends(require_seconds)  # checks credits before work
):
    """Returns JSON with preview_url/final_url (+ optional transcript)."""
    try:
        # Resolve source
        if file is not None:
            src = os.path.join(UPLOAD_DIR, safe(file.filename))
            with open(src, "wb") as f: shutil.copyfileobj(file.file, f)
        elif url:
            tmp = await download_to_tmp(url)
            src = os.path.join(UPLOAD_DIR, safe(os.path.basename(url) or f"remote_{datetime.utcnow().timestamp()}.mp4"))
            shutil.copy(tmp, src); os.remove(tmp)
        else:
            return JSONResponse({"ok": False, "error": "Provide file or url."}, 400)

        want_final = (final_1080 == "1")
        want_transcript = (include_transcript == "1")
        wm = wm_text if watermark == "1" else None

        # Charge seconds = duration of the requested slice
        from utils import seconds_between
        sec = seconds_between(start, end)
        charge_seconds(user["id"], sec)

        out = await make_clip_bundle(
            request, src, start.strip(), end.strip(),
            want_preview=True, want_final=want_final,
            watermark_text=wm,
            transcribe_clip=want_transcript,
            user_id=user["id"], user_email=user.get("email")
        )
        return JSONResponse({"ok": True, **out})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

# Back-compat: returns the preview MP4 blob directly
@app.post("/clip")
async def clip_endpoint(
    start: str = Form(...),
    end: str   = Form(...),
    watermark: str = Form("0"),
    wm_text: str   = Form("@ClipForge"),
    file: UploadFile = File(...),
    user = Depends(require_user),
    _ = Depends(require_seconds)
):
    try:
        src = os.path.join(UPLOAD_DIR, safe(file.filename))
        with open(src, "wb") as f: shutil.copyfileobj(file.file, f)

        from utils import seconds_between
        sec = seconds_between(start, end)
        charge_seconds(user["id"], sec)

        out = await make_clip_bundle(
            request=None,  # not needed for FileResponse
            src_path=src, start=start.strip(), end=end.strip(),
            want_preview=True, want_final=False,
            watermark_text=(wm_text if watermark == "1" else None),
            transcribe_clip=False,
            user_id=user["id"], user_email=user.get("email"),
        )
        if not out.get("preview_url"):
            return JSONResponse({"ok": False, "error": "No preview generated."}, 500)
        # Map back to file path
        preview_file = os.path.join(PREVIEW_DIR, os.path.basename(out["preview_url"]))
        return FileResponse(preview_file, filename=os.path.basename(preview_file), media_type="video/mp4")
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

@app.post("/clip_multi")
async def clip_multi(
    request: Request,
    sections: str = Form(...),  # JSON list of {"start","end"}
    watermark: str = Form("0"),
    wm_text: str   = Form("@ClipForge"),
    preview_480: str = Form("1"),
    final_1080: str  = Form("0"),
    include_transcript: str = Form("0"),
    file: UploadFile = File(None),
    url: str = Form(None),
    user = Depends(require_user),
    _ = Depends(require_seconds)
):
    tmp = None
    try:
        # Resolve source
        if file is not None:
            src = os.path.join(UPLOAD_DIR, safe(file.filename))
            with open(src, "wb") as f: f.write(await file.read())
        elif url:
            tmp = await download_to_tmp(url)
            src = os.path.join(UPLOAD_DIR, safe(os.path.basename(url) or f"remote_{datetime.utcnow().timestamp()}.mp4"))
            shutil.copy(tmp, src)
        else:
            return JSONResponse({"ok": False, "error": "Provide a file or a url."}, 400)

        try:
            segs = json.loads(sections)
        except Exception:
            return JSONResponse({"ok": False, "error": "sections must be JSON list"}, 400)
        if not isinstance(segs, list) or not segs:
            return JSONResponse({"ok": False, "error": "sections must be non-empty list"}, 400)

        want_prev  = (preview_480 == "1")
        want_final = (final_1080 == "1")
        want_transcript = (include_transcript == "1")
        wm = (wm_text if watermark == "1" else None)

        # Charge sum of all durations
        from utils import seconds_between
        total_sec = 0
        for s in segs:
            total_sec += max(0, seconds_between(str(s.get("start","")), str(s.get("end",""))))
        if total_sec > 0:
            charge_seconds(user["id"], total_sec)

        sem = asyncio.Semaphore(3)
        async def worker(s, e):
            async with sem:
                r = await make_clip_bundle(
                    request, src, s.strip(), e.strip(),
                    want_preview=want_prev, want_final=want_final,
                    watermark_text=wm,
                    transcribe_clip=want_transcript,
                    user_id=user["id"], user_email=user.get("email")
                )
                return r

        tasks = [worker(str(s.get("start","")), str(s.get("end",""))) for s in segs]
        results = await asyncio.gather(*tasks)

        zip_url = None
        if want_final:
            zip_name = f"clips_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}.zip"
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
        except Exception:
            pass

# =========================================================
# Transcribe (URL/File)
# =========================================================
@app.post("/transcribe")
async def transcribe_audio(
    url: str = Form(None),
    file: UploadFile = File(None),
    user = Depends(require_user),
    _ = Depends(require_seconds)
):
    if not client:
        return JSONResponse({"ok": False, "error": "OPENAI_API_KEY missing"}, 500)
    from utils import to_mp3_for_whisper
    tmp_path = None
    mp3_path = None
    try:
        if file:
            import os
            suffix = os.path.splitext(file.filename)[1] or ".webm"
            tmp_path = os.path.join(TMP_DIR, f"upl_{datetime.utcnow().timestamp()}{suffix}")
            with open(tmp_path, "wb") as f: f.write(await file.read())
        elif url:
            tmp_path = await download_to_tmp(url)
        else:
            return JSONResponse({"ok": False, "error": "No file or URL provided."}, 400)

        mp3_path = await to_mp3_for_whisper(tmp_path)
        with open(mp3_path, "rb") as a:
            t = client.audio.transcriptions.create(model="whisper-1", file=a, response_format="text")
        text_output = t.strip() if isinstance(t, str) else str(t) or "(no text)"

        # bill: estimate by audio length
        sec = ffprobe_duration(mp3_path) or 0
        if sec > 0:
            charge_seconds(user["id"], int(sec))

        # best-effort DB
        try:
            upsert_video_row(user_id=user["id"], src_url="transcribe", duration_sec=int(sec), transcript=text_output)
        except Exception:
            pass

        return JSONResponse({"ok": True, "text": text_output})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    finally:
        for p in (tmp_path, mp3_path):
            try:
                if p and os.path.exists(p): os.remove(p)
            except Exception:
                pass

# =========================================================
# AI helpers
# =========================================================
SYSTEM_PROMPT = (
    "You are ClipForge AI, an editing copilot. "
    "Be concise and practical. When asked to find moments, "
    "suggest 10–45s ranges using HH:MM:SS."
)

@app.post("/ai_chat")
async def ai_chat(
    user_message: str = Form(...),
    transcript: str = Form(""),
    history: str = Form("[]"),
    user = Depends(require_user)
):
    if not client:
        return JSONResponse({"ok": False, "error": "OPENAI_API_KEY missing"}, 500)
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
async def auto_clip(transcript: str = Form(...), max_clips: int = Form(3), user = Depends(require_user)):
    if not client:
        return JSONResponse({"ok": False, "error": "OPENAI_API_KEY missing"}, 500)
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
