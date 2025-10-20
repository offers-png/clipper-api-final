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
                print(f"yt_dlp failed: {e}")
                print("➡️ Switching to Piped API fallback...")

                # ✅ 3 fallback APIs
                piped_instances = [
                    "https://pipedapi.adminforge.de",
                    "https://pipedapi.syncpundit.com",
                    "https://pipedapi.mha.fi"
                ]

                stream_data = None
                for api in piped_instances:
                    try:
                        res = requests.get(f"{api}/streams/{video_id}", timeout=10)
                        if res.status_code == 200:
                            stream_data = res.json()
                            break
                    except Exception:
                        continue

                if not stream_data:
                    raise ValueError("All Piped API endpoints failed.")

                mp4_stream = next(
                    (s for s in stream_data.get("videoStreams", []) if "mp4" in s["mimeType"]),
                    None
                )
                if not mp4_stream:
                    raise ValueError("No MP4 stream available from Piped API.")

                video_url = mp4_stream["url"]
                with requests.get(video_url, stream=True) as r:
                    r.raise_for_status()
                    with open(input_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)

            # ✅ Trim video
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
