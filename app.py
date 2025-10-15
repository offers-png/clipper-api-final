from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional
import os
import shutil
import tempfile
import subprocess
import re

# ---------------------------------------------------------
# 🚀 Clipper AI Backend - Full Version with Transcription
# ---------------------------------------------------------

app = FastAPI(title="Clipper Agent", version="1.2.0")

# ✅ Enable CORS (frontend connection)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# 🩺 Health routes
# ---------------------------------------------------------
@app.get("/")
def root():
    return {"ok": True, "message": "Clipper API"}

@app.get("/health")
def health():
    return {"ok": True, "moviepy": True}

# ---------------------------------------------------------
# 🎬 Video clipping logic
# ---------------------------------------------------------
USE_MOVIEPY = True
try:
    from moviepy.editor import VideoFileClip, CompositeVideoClip, ImageClip
except Exception:
    USE_MOVIEPY = False

def ensure_ffmpeg():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    return ffmpeg

def overlay_with_ffmpeg(input_path: str, output_path: str, watermark_path: Optional[str] = None):
    ffmpeg = ensure_ffmpeg()
    if watermark_path and os.path.exists(watermark_path):
        cmd = [
            ffmpeg, "-y", "-i", input_path, "-i", watermark_path,
            "-filter_complex", "overlay=10:10", "-c:a", "copy", output_path
        ]
    else:
        cmd = [
            ffmpeg, "-y", "-i", input_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", output_path
        ]
    run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if run.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {run.stderr.decode(errors='ignore')[:300]}")

def overlay_with_moviepy(input_path: str, output_path: str, watermark_path: Optional[str] = None):
    clip = VideoFileClip(input_path)
    if watermark_path and os.path.exists(watermark_path):
        wm = ImageClip(watermark_path).set_duration(clip.duration).set_pos(("left", "top"))
        final = CompositeVideoClip([clip, wm])
    else:
        final = clip
    final.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac",
        threads=os.cpu_count() or 2,
        logger=None
    )
    clip.close()
    if 'wm' in locals():
        wm.close()

@app.post("/clip")
async def clip_video(file: UploadFile = File(...)):
    try:
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "input.mp4")
            out_path = os.path.join(td, "output.mp4")
            wm_path = os.path.join(os.path.dirname(__file__), "watermark.png")

            with open(src_path, "wb") as f:
                f.write(await file.read())

            if USE_MOVIEPY:
                overlay_with_moviepy(src_path, out_path, wm_path)
            else:
                overlay_with_ffmpeg(src_path, out_path, wm_path)

            return FileResponse(out_path, media_type="video/mp4", filename="clipped.mp4")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)[:700]})

# ---------------------------------------------------------
# 🎧 YouTube Transcription Endpoint (Fixed)
# ---------------------------------------------------------
from youtube_transcript_api import YouTubeTranscriptApi as yta

@app.post("/transcribe")
async def transcribe(payload: dict):
    video_url = payload.get("url") or payload.get("video_url")
    if not video_url:
        return JSONResponse(status_code=400, content={"error": "Missing video URL"})

    match = re.search(r"(?:v=|be/)([a-zA-Z0-9_-]{11})", video_url)
    if not match:
        return JSONResponse(status_code=400, content={"error": "Invalid YouTube URL"})
    video_id = match.group(1)

    try:
        # ✅ Correct API method for current version
        transcript_obj = yta.list_transcripts(video_id)
        transcript = transcript_obj.find_transcript(['en']).fetch()
        full_text = " ".join([entry["text"] for entry in transcript])

        return {
            "ok": True,
            "url": video_url,
            "transcript": full_text[:5000]
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ---------------------------------------------------------
# ✅ Run locally if needed
# ---------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
