import os
import shutil
import tempfile
import subprocess
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional

app = FastAPI(title="Clipper Agent", version="1.0.0")

# Try to import MoviePy, fallback to FFmpeg
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
        cmd = [ffmpeg, "-y", "-i", input_path, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", output_path]
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
    final.write_videofile(output_path, codec="libx264", audio_codec="aac", threads=os.cpu_count() or 2, logger=None)
    clip.close()
    if 'wm' in locals():
        wm.close()

@app.get("/health")
def health():
    return {"ok": True, "moviepy": USE_MOVIEPY}

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
