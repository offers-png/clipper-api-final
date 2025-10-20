import os, subprocess, yt_dlp
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

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

def run_ffmpeg(input_path, start, end, output_path):
    cmd = [
        "ffmpeg", "-y", "-ss", start, "-to", end,
        "-i", input_path, "-c", "copy", output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
        input_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

        cookies_path = "cookies.txt"  # ✅ Uses your uploaded file

        # ✅ yt_dlp options
        ydl_opts = {
            "outtmpl": input_path,
            "format": "best[ext=mp4]/mp4",
            "quiet": True,
            "noplaylist": True,
        }

        # ✅ Use cookies if present
        if os.path.exists(cookies_path):
            ydl_opts["cookiefile"] = cookies_path
        else:
            print("⚠️ No cookies.txt found. Running public mode.")

        # ✅ Download video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # ✅ Trim it
        run_ffmpeg(input_path, start, end, output_path)

        if not os.path.exists(output_path):
            return JSONResponse({"error": "Trim failed."}, status_code=500)

        return FileResponse(output_path, media_type="video/mp4", filename=f"trimmed_{video_id}.mp4")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
