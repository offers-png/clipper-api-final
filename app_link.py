import os, subprocess
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI()

origins = ["https://ptsel-frontend.onrender.com", "http://localhost:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def safe_run(cmd):
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except Exception as e:
        print("❌ Command failed:", e)
        return False

def download_any(url, output):
    """ Try yt-dlp → streamlink → ffmpeg (fallback chain). """
    # 1️⃣ Try yt-dlp first
    ydl_opts = {
        "outtmpl": output,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "geo_bypass": True,
        "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        if os.path.exists(output):
            return True
    except Exception as e:
        print("yt_dlp failed:", e)

    # 2️⃣ Fallback: Streamlink (for live streams, radio, m3u8)
    if safe_run([
        "streamlink", "--default-stream", "best", "--stdout", url,
        "|", "ffmpeg", "-y", "-i", "pipe:0", "-c", "copy", output
    ]):
        if os.path.exists(output):
            return True

    # 3️⃣ Fallback: ffmpeg direct download or capture
    if safe_run(["ffmpeg", "-y", "-i", url, "-c", "copy", output]):
        return True

    return False


def trim_video(input_path, start, end, output_path):
    if not safe_run(["ffmpeg", "-y", "-ss", start, "-to", end,
                     "-i", input_path, "-c", "copy", output_path]):
        safe_run(["ffmpeg", "-y", "-ss", start, "-to", end,
                  "-i", input_path, "-c:v", "libx264", "-preset", "veryfast",
                  "-crf", "23", "-c:a", "aac", output_path])


@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        file_id = url.split("/")[-1].split("?")[0]
        input_path = os.path.join(UPLOAD_DIR, f"{file_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{file_id}.mp4")

        ok = download_any(url, input_path)
        if not ok:
            return JSONResponse({"error": "❌ Unable to fetch that link. It may be private or DRM-protected."}, status_code=400)

        trim_video(input_path, start, end, output_path)
        return FileResponse(output_path, media_type="video/mp4", filename=f"trimmed_{file_id}.mp4")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
