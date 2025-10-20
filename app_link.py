import os
import subprocess
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
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

# ✅ Upload directory
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ✅ Health check route (required by Render)
@app.get("/")
def root():
    return {"status": "Clipper API running on Render ✅"}

# ✅ Helper: run ffmpeg safely
def run_ffmpeg(input_path, start, end, output_path):
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-y", "-ss", start, "-to", end,
        "-i", input_path, "-c", "copy", output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# ✅ Main YouTube Clipping route
@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
        input_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

        # ✅ yt_dlp setup with cookies.txt
        ydl_opts = {
            "outtmpl": input_path,
            "format": "best[ext=mp4]/mp4",
            "quiet": True,
            "noplaylist": True,
            "cookiefile": "cookies.txt",  # 👈 this line fixes the login wall
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        run_ffmpeg(input_path, start, end, output_path)
        return FileResponse(output_path, media_type="video/mp4", filename=f"trimmed_{video_id}.mp4")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
