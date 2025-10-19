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
async def clip_youtube(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        yt_path = os.path.join(UPLOAD_DIR, "yt_source.mp4")
        ydl_opts = {"outtmpl": yt_path, "format": "mp4"}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        trimmed_path = os.path.join(UPLOAD_DIR, "yt_trimmed.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-ss", start,
            "-to", end,
            "-i", yt_path,
            "-c:v", "libx264",
            "-c:a", "aac",
            trimmed_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0:
            return JSONResponse({"error": result.stderr.decode("utf-8")}, status_code=500)

        return FileResponse(trimmed_path, filename="yt_trimmed.mp4")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
