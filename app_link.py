import os, subprocess, requests
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI()

# ✅ Allow frontend
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

def run_ffmpeg(input_path, start, end, output_path):
    cmd = [
        "ffmpeg", "-y", "-ss", start, "-to", end,
        "-i", input_path, "-c", "copy", output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

@app.get("/")
def root():
    return {"status": "Clipper API running on Render ✅"}

# ✅ New YouTube Fetch endpoint (TubeFetch logic)
@app.post("/fetch")
async def fetch_video(url: str = Form(...)):
    try:
        filename = os.path.join(UPLOAD_DIR, "video.mp4")
        ydl_opts = {
            "outtmpl": filename,
            "format": "best[ext=mp4]/mp4",
            "quiet": True,
            "noplaylist": True,
            "nocheckcertificate": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        title = info.get("title", "video")
        return JSONResponse({
            "title": title,
            "download": f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', '')}/download"
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/download")
def download_file():
    filepath = os.path.join(UPLOAD_DIR, "video.mp4")
    if os.path.exists(filepath):
        return FileResponse(filepath, media_type="video/mp4", filename="video.mp4")
    return JSONResponse({"error": "File not found"}, status_code=404)

# ✅ Clipping route (with start & end)
@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
        input_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

        # Download video
        ydl_opts = {
            "outtmpl": input_path,
            "format": "best[ext=mp4]/mp4",
            "quiet": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Trim with ffmpeg
        run_ffmpeg(input_path, start, end, output_path)
        return FileResponse(output_path, media_type="video/mp4", filename=f"trimmed_{video_id}.mp4")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
