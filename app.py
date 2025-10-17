from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import subprocess
import os
import time
import threading

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
CLIP_DIR = os.path.join(BASE_DIR, "clips")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLIP_DIR, exist_ok=True)


def auto_clean():
    while True:
        try:
            now = time.time()
            for folder in [UPLOAD_DIR, CLIP_DIR]:
                for f in os.listdir(folder):
                    p = os.path.join(folder, f)
                    if os.path.isfile(p) and now - os.path.getmtime(p) > 24 * 3600:
                        os.remove(p)
                        print(f"🧹 deleted old file {p}")
        except Exception as e:
            print("cleanup error", e)
        time.sleep(3600)

threading.Thread(target=auto_clean, daemon=True).start()


@app.get("/")
async def root():
    return {"status": "PTSEL Clipper running ✅"}


@app.post("/trim")
async def trim_video(
    file: UploadFile = File(None),
    url: str = Form(None),
    start: str = Form(...),
    end: str = Form(...),
):
    try:
        ts = int(time.time())
        in_path = os.path.join(UPLOAD_DIR, f"input_{ts}.mp4")
        out_name = f"output_{ts}.mp4"
        out_path = os.path.join(CLIP_DIR, out_name)

        # save upload
        if file:
            with open(in_path, "wb") as f:
                f.write(await file.read())
        elif url:
            os.system(f"yt-dlp -f mp4 {url} -o {in_path}")
        else:
            raise HTTPException(status_code=400, detail="no file or url provided")

        cmd = [
            "ffmpeg", "-y",
            "-ss", start,
            "-to", end,
            "-i", in_path,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            out_path
        ]

        print("🎬 trimming...")
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="ffmpeg failed")

        if not os.path.exists(out_path):
            raise HTTPException(status_code=500, detail="output not created")

        print(f"✅ saved {out_name}")
        return JSONResponse({
            "message": "clip ready",
            "download_url": f"https://clipper-api-final.onrender.com/clips/{out_name}"
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/clips/{filename}")
async def get_clip(filename: str):
    path = os.path.join(CLIP_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path, media_type="video/mp4", filename=filename)
    raise HTTPException(status_code=404, detail="Clip not found")
