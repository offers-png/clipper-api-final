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
                # ✅ Extract video ID
                video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]

                # ✅ Use a public ytproxy API (no login/cookies needed)
                proxy_api = f"https://api.ytdlproxy.workers.dev/?id={video_id}"

                res = requests.get(proxy_api, timeout=10)
                data = res.json()

                video_url = data.get("url")
                if not video_url:
                    raise ValueError("Failed to get direct video URL from proxy API.")

                temp_input = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
                temp_output = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

                # Download and trim
                with requests.get(video_url, stream=True) as r:
                    r.raise_for_status()
                    with open(temp_input, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)

                run_ffmpeg(temp_input, start, end, temp_output)

                return FileResponse(
                    temp_output,
                    media_type="video/mp4",
                    filename=f"trimmed_{video_id}.mp4"
                )

            except Exception as e:
                return JSONResponse({"error": f"Proxy YouTube fetch failed: {str(e)}"}, status_code=500)

        # --- Non-YouTube direct video URLs ---
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
