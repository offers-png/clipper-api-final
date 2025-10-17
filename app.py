from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import subprocess, os, time, threading

# ======================================================
# APP SETUP
# ======================================================

app = FastAPI(title="PTSEL Clipper API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # change to your frontend domain later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================================================
# PATH SETUP (Persistent Disk)
# ======================================================

BASE = os.getenv("CLIP_STORAGE_PATH", "/data")  # persistent disk
UPLOAD_DIR = os.path.join(BASE, "uploads")
CLIP_DIR = os.path.join(BASE, "clips")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLIP_DIR, exist_ok=True)

# ======================================================
# AUTO CLEAN (every 24 hours)
# ======================================================

def auto_clean():
    while True:
        try:
            now = time.time()
            for folder in [UPLOAD_DIR, CLIP_DIR]:
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    if os.path.isfile(path) and now - os.path.getmtime(path) > 24 * 3600:
                        os.remove(path)
                        print(f"🧹 Deleted old file: {path}")
        except Exception as e:
            print(f"Cleanup error: {e}")
        time.sleep(3600)

threading.Thread(target=auto_clean, daemon=True).start()

# ======================================================
# ROUTES
# ======================================================

@app.get("/")
async def root():
    return {"status": "PTSEL Clipper Stable 🚀", "storage": BASE}


@app.post("/trim")
async def trim_video(
    file: UploadFile = File(None),
    url: str = Form(None),
    start: str = Form(...),
    end: str = Form(...)
):
    try:
        timestamp = int(time.time())
        input_path = os.path.join(UPLOAD_DIR, f"input_{timestamp}.mp4")
        output_name = f"output_{timestamp}.mp4"
        output_path = os.path.join(CLIP_DIR, output_name)

        # ======================================================
        # STEP 1: SAVE UPLOADED FILE OR DOWNLOAD YOUTUBE
        # ======================================================
        if file:
            contents = await file.read()
            if len(contents) > 3 * 1024 * 1024 * 1024:  # >3GB
                raise HTTPException(status_code=413, detail="File too large (max 3GB).")
            with open(input_path, "wb") as f:
                f.write(contents)
        elif url:
            os.system(f"yt-dlp -f mp4 -o '{input_path}' '{url}'")
        else:
            raise HTTPException(status_code=400, detail="No file or URL provided.")

        # ======================================================
        # STEP 2: FFMPEG INSTANT CLIP (no re-encode)
        # ======================================================
        cmd = [
            "ffmpeg", "-y",
            "-ss", start,
            "-to", end,
            "-i", input_path,
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path
        ]

        print(f"🎬 Clipping {input_path} → {output_path}")
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0 or not os.path.exists(output_path):
            print(result.stderr.decode())
            raise HTTPException(status_code=500, detail="FFmpeg failed or clip not created.")

        print(f"✅ Clip complete: {output_path}")

        return JSONResponse({
            "message": "Clip created successfully.",
            "download_url": f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/clips/{output_name}"
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/clips/{filename}")
async def get_clip(filename: str):
    path = os.path.join(CLIP_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path, media_type="video/mp4", filename=filename)
    raise HTTPException(status_code=404, detail="Clip not found")
