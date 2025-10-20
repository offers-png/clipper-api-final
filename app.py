from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import subprocess
import yt_dlp

app = FastAPI()

# ✅ CORS setup
origins = [
    "https://ptsel-frontend.onrender.com",
    "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Upload folder
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ✅ FFmpeg helper
def run_ffmpeg(input_path, start, end, output_path):
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", start,
        "-to", end,
        "-i", input_path,
        "-c", "copy",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# ✅ YouTube or direct URL clip route
@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
        input_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

        # --- YOUTUBE LINKS ---
        if "youtube.com" in url or "youtu.be" in url:
            try:
                print("Attempting YouTube download via yt_dlp...")
                ydl_opts = {
                    "outtmpl": input_path,
                    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4",
                    "quiet": True,
                    "noplaylist": True,
                    "nocheckcertificate": True,
                    "geo_bypass": True,
                    "retries": 3,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as e:
                # --- Retry with cookies bypass ---
                print(f"yt_dlp failed, retrying with bypass... {e}")
                ydl_opts["cookiefile"] = None
                ydl_opts["source_address"] = "0.0.0.0"
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

            # ✅ Trim
            run_ffmpeg(input_path, start, end, output_path)

            return FileResponse(
                output_path,
                media_type="video/mp4",
                filename=f"trimmed_{video_id}.mp4"
            )

        # --- DIRECT VIDEO LINKS ---
        else:
            filename = os.path.join(UPLOAD_DIR, "temp_video.mp4")
            output_path = os.path.join(UPLOAD_DIR, "trimmed_output.mp4")

            with requests.get(url, stream=True) as r:
                r.raise_for_status()
                with open(filename, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            run_ffmpeg(filename, start, end, output_path)

            return FileResponse(
                output_path,
                media_type="video/mp4",
                filename="trimmed_output.mp4"
            )

    except Exception as e:
        return JSONResponse({"error": f"Server error: {str(e)}"}, status_code=500)
