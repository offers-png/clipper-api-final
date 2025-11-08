import os, shutil, asyncio, subprocess, tempfile, json, requests
from datetime import datetime, timedelta
from zipfile import ZipFile

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from openai import OpenAI
from supabase import create_client, Client

# =====================
# App & Clients
# =====================
app = FastAPI(title="ClipForge AI API", version="1.2.0")
client = OpenAI()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

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

# =====================
# Housekeeping
# =====================
def auto_cleanup():
    now = datetime.now()
    cutoff = (now - timedelta(days=3)).timestamp()
    removed = 0
    for root, _, files in os.walk(UPLOAD_DIR):
        for name in files:
            p = os.path.join(root, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p); removed += 1
            except: pass
    if removed:
        print(f"🧹 Removed {removed} expired files")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(asyncio.to_thread(auto_cleanup))

@app.get("/")
def health():
    return {"ok": True, "msg": "✅ ClipForge AI API running"}

# =====================
# Helpers
# =====================
def _yt_url(u: str) -> bool:
    u = (u or "").lower()
    return any(k in u for k in ["youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com"])

def _download_media_to(path: str, url: str) -> None:
    """
    Try yt-dlp for social URLs; if not, do a simple GET stream.
    Raises Exception on failure.
    """
    if _yt_url(url):
        proc = subprocess.run(
            ["yt-dlp", "-f", "mp4", "-o", path, url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300
        )
        if proc.returncode != 0 or not os.path.exists(path):
            raise Exception(f"yt-dlp failed: {proc.stderr[:400]}")
        return

    # Direct URL fallback
    with requests.get(url, stream=True, timeout=60) as r:
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code} when downloading")
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)

def _ffmpeg_trim(input_path: str, out_path: str, start: str, end: str, *, fast: bool, wm_text: str):
    vf = []
    if wm_text:
        draw = (
            "drawtext=text='{t}':x=w-tw-20:y=h-th-20:"
            "fontcolor=white:fontsize=28:box=1:boxcolor=black@0.45:boxborderw=10"
        ).format(t=wm_text.replace("'", r"\'"))
        vf = ["-vf", draw]

    if fast and not vf:
        cmd = ["ffmpeg","-hide_banner","-loglevel","error","-ss",start,"-to",end,"-i",input_path,
               "-c:v","copy","-c:a","aac","-b:a","192k","-y",out_path]
    else:
        cmd = ["ffmpeg","-hide_banner","-loglevel","error","-ss",start,"-to",end,"-i",input_path,
               "-c:v","libx264","-preset","veryfast","-c:a","aac","-b:a","192k","-y"] + vf + [out_path]

    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1800)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise Exception(r.stderr[:400])

# =====================
# Clip Endpoints
# =====================
@app.post("/clip")
async def clip_video(
    file: UploadFile = File(...),
    start: str = Form(...),
    end: str = Form(...),
    watermark: str = Form("0"),
    wm_text: str = Form(""),
    fast: str = Form("1"),
):
    try:
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        base, _ = os.path.splitext(file.filename)
        out_path = os.path.join(UPLOAD_DIR, f"{base}_{start.replace(':','-')}-{end.replace(':','-')}.mp4")
        _ffmpeg_trim(input_path, out_path, start, end, fast=(fast=="1"), wm_text=(wm_text if watermark=="1" else ""))

        return FileResponse(out_path, filename=os.path.basename(out_path), media_type="video/mp4")
    except subprocess.TimeoutExpired:
        return JSONResponse({"error":"FFmpeg timed out"}, 504)
    except Exception as e:
        return JSONResponse({"error": f"FFmpeg failed: {str(e)}"}, 500)

@app.post("/clip_multi")
async def clip_multi(
    file: UploadFile = File(...),
    sections: str = Form(...),
    watermark: str = Form("0"),
    wm_text: str = Form(""),
    fast: str = Form("1"),
):
    try:
        data = json.loads(sections)
        if not isinstance(data, list) or not data:
            return JSONResponse({"error":"sections must be a JSON array"}, 400)

        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        zip_path = os.path.join(UPLOAD_DIR, "clips_bundle.zip")
        if os.path.exists(zip_path): os.remove(zip_path)

        with ZipFile(zip_path, "w") as zipf:
            for i, sec in enumerate(data, 1):
                s = str(sec.get("start","")).strip()
                e = str(sec.get("end","")).strip()
                if not s or not e:
                    return JSONResponse({"error": f"Missing start/end in section {i}"}, 400)

                out_name = f"clip_{i}.mp4"
                out_path = os.path.join(UPLOAD_DIR, out_name)
                _ffmpeg_trim(input_path, out_path, s, e, fast=(fast=="1"), wm_text=(wm_text if watermark=="1" else ""))
                zipf.write(out_path, arcname=out_name)

        return FileResponse(zip_path, media_type="application/zip", filename="clips_bundle.zip")
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

# =====================
# AI Helper
# =====================
SYSTEM_PROMPT = (
    "You are ClipForge AI, an editing copilot. Be concise and practical. "
    "When asked to find moments, suggest 10–45s ranges. If a transcript is provided, reference it."
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
            msgs.append({"role":"system","content":f"Transcript:\n{transcript[:12000]}"})
        try:
            prior = json.loads(history) if history else []
            if isinstance(prior, list): msgs.extend(prior)
        except: pass
        msgs.append({"role":"user","content":user_message})

        r = client.chat.completions.create(model="gpt-4o-mini", temperature=0.3, messages=msgs)
        return {"reply": r.choices[0].message.content.strip()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

@app.post("/auto_clip")
async def auto_clip(transcript: str = Form(...), max_clips: int = Form(3)):
    try:
        prompt = (
            "From this transcript, pick up to {k} short, high-impact moments (10–45s). "
            "Return JSON: {{\"clips\":[{{\"start\":\"HH:MM:SS\",\"end\":\"HH:MM:SS\",\"summary\":\"...\"}}]}}.\n\n"
            "Transcript:\n{t}"
        ).format(k=max_clips, t=transcript[:12000])

        r = client.chat.completions.create(
            model="gpt-4o-mini", temperature=0.2,
            messages=[{"role":"user","content":prompt}]
        )
        raw = r.choices[0].message.content
        try:
            data = json.loads(raw)
        except:
            a, b = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[a:b+1]) if a!=-1 and b!=-1 else {"clips":[]}

        out = []
        for c in data.get("clips", [])[:max_clips]:
            out.append({
                "start": str(c.get("start","00:00:00")),
                "end":   str(c.get("end","00:00:10")),
                "summary": str(c.get("summary",""))[:140]
            })
        return {"clips": out}
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

# =====================
# Transcribe
# =====================
@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(None),
    url: str = Form(None),
    user_email: str = Form("guest@clipforge.ai"),
):
    tmp = None; audio_mp3 = None
    try:
        os.makedirs("/tmp", exist_ok=True)

        if file:
            suf = os.path.splitext(file.filename)[1] or ".mp4"
            tmp = os.path.join("/tmp", f"upl_{datetime.now().timestamp()}{suf}")
            with open(tmp, "wb") as f:
                f.write(await file.read())
        elif url:
            tmp = os.path.join("/tmp", f"remote_{datetime.now().timestamp()}.mp4")
            _download_media_to(tmp, url)
        else:
            return JSONResponse({"error":"No file or URL provided."}, 400)

        audio_mp3 = tmp.rsplit(".",1)[0] + ".mp3"
        p = subprocess.run(
            ["ffmpeg","-y","-i",tmp,"-vn","-acodec","libmp3lame","-b:a","192k",audio_mp3],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if p.returncode != 0 or not os.path.exists(audio_mp3):
            return JSONResponse({"error":"FFmpeg failed to create audio"}, 500)

        with open(audio_mp3,"rb") as f:
            t = client.audio.transcriptions.create(model="whisper-1", file=f, response_format="text")

        text = t.strip() if isinstance(t,str) else str(t)
        if not text: text = "(no text found)"

        # Save to Supabase
        try:
            res = supabase.table("transcriptions").insert({
                "user_email": user_email,
                "text": text,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
            tid = res.data[0]["id"] if res.data else None
        except Exception as e:
            print("Supabase insert error:", e)
            tid = None

        return {"text": text, "transcription_id": tid}
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)
    finally:
        for pth in [tmp, audio_mp3]:
            try:
                if pth and os.path.exists(pth): os.remove(pth)
            except: pass
