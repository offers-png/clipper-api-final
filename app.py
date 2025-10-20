from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, JSONResponse
import requests
import yt_dlp
import os
import subprocess
import tempfile

app = FastAPI()

# --- Existing upload dir setup ---
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- Helper function ---
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

@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_path = None
        output_path = None

        # --- STEP 1: Handle YouTube via Piped API ---
        if "youtube.com" in url or "youtu.be" in url:
            try:
                video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
                api_url = f"https://pipedapi.kavin.rocks/streams/{video_id}"
                data = requests.get(api_url, timeout=10).json()

                # Pick best quality stream (mp4)
                stream = next(
                    (s for s in data.get("videoStreams", []) if "mp4" in s["mimeType"]),
                    None
                )
                if not stream:
                    return JSONResponse({"error": "No valid MP4 stream found."}, status_code=400)

                video_url = stream["url"]
                temp_input = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
                temp_output = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

                # Download the stream
                with requests.get(video_url, stream=True) as r:
                    r.raise_for_status()
                    with open(temp_input, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)

                # --- STEP 2: Trim it ---
                run_ffmpeg(temp_input, start, end, temp_output)

                # --- STEP 3: Return file ---
                return FileResponse(
                    temp_output,
                    media_type="video/mp4",
                    filename=f"trimmed_{video_id}.mp4"
                )
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=500)

        # --- Non-YouTube (Direct URL) ---
        else:
            filename = os.path.join(UPLOAD_DIR, "temp_video.mp4")
            output_path = os.path.join(UPLOAD_DIR, "trimmed_output.mp4")

            # Download direct link
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
        return JSONResponse({"error": str(e)}, status_code=500)
