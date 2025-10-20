from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import subprocess

app = FastAPI()

# ✅ Allow requests from your frontend (CORS setup)
origins = [
    "https://ptsel-frontend.onrender.com",  # your Render frontend
    "http://localhost:5173",                # optional local dev
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Upload folder setup ---
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- Helper function to trim video ---
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

# --- YouTube & Direct URL Handler ---
@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_id = None
        temp_input = None
        temp_output = None

        # --- YouTube Links ---
        if "youtube.com" in url or "youtu.be" in url:
            try:
                # ✅ Try Piped API first
                video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
                api_url = f"https://pipedapi.kavin.rocks/streams/{video_id}"
                r = requests.get(api_url, timeout=10)
                if not r.text.strip():
                    raise ValueError("Empty Piped API response.")
                data = r.json()

                # Find best MP4 stream
                stream = next(
                    (s for s in data.get("videoStreams", []) if "mp4" in s["mimeType"]),
                    None
                )
                if not stream:
                    raise ValueError("No valid MP4 stream in Piped API.")

                video_url = stream["url"]
                temp_input = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
                temp_output = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

                # Download file
                with requests.get(video_url, stream=True) as r:
                    r.raise_for_status()
                    with open(temp_input, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)

            except Exception as piped_error:
                # ⚠️ Fallback: use yt_dlp directly
                print(f"Piped failed: {piped_error}. Falling back to yt_dlp...")
                temp_input = os.path.join(UPLOAD_DIR, "yt_fallback.mp4")
                temp_output = os.path.join(UPLOAD_DIR, "yt_trimmed.mp4")

                import yt_dlp
                ydl_opts = {
                    "outtmpl": temp_input,
                    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4",
                    "quiet": True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

            # ✅ Trim video
            run_ffmpeg(temp_input, start, end, temp_output)

            return FileResponse(
                temp_output,
                media_type="video/mp4",
                filename=f"trimmed_{video_id or 'youtube'}.mp4"
            )

        # --- Direct video links ---
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
