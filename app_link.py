import os
import subprocess
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

def download_media(url, input_path):
    """
    Handles YouTube, TikTok, Twitter, Facebook, Vimeo, and others.
    Falls back to ffmpeg for streams or radio links.
    """
    ydl_opts = {
        "outtmpl": input_path,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
        "geo_bypass": True,
        "ignoreerrors": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        print("yt_dlp failed:", e)
        # Try ffmpeg fallback for direct/stream URLs
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", url, "-c", "copy", input_path
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return True
        except Exception as e2:
            print("ffmpeg fallback failed:", e2)
            return False

def trim_video(input_path, start, end, output_path):
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", start,
            "-to", end,
            "-i", input_path,
            "-c", "copy", output_path
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except:
        # Re-encode fallback if direct copy fails
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", start,
            "-to", end,
            "-i", input_path,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            output_path
        ], check=True)
        return True

@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
        input_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

        success = download_media(url, input_path)
        if not success:
            return JSONResponse({"error": "Could not download this link"}, status_code=400)

        trim_video(input_path, start, end, output_path)
        return FileResponse(output_path, media_type="video/mp4", filename=f"trimmed_{video_id}.mp4")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
