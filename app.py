from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, File, Form, UploadFile, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import os, shutil, time, subprocess, threading

app = FastAPI()

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # change to your frontend domain when ready
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Persistent paths (Render disk mounted at /data) ---
BASE = "/data" if os.path.isdir("/data") else os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(BASE, "chunks")   # per-upload chunk folders
UPLOADS_DIR = os.path.join(BASE, "uploads") # merged originals
CLIPS_DIR   = os.path.join(BASE, "clips")   # finished clips
for d in (CHUNKS_DIR, UPLOADS_DIR, CLIPS_DIR):
    os.makedirs(d, exist_ok=True)

# --- light hourly janitor to keep disk tidy (delete >48h files) ---
def _janitor():
    while True:
        cutoff = time.time() - 48*3600
        for root in (CHUNKS_DIR, UPLOADS_DIR, CLIPS_DIR):
            for name in os.listdir(root):
                p = os.path.join(root, name)
                try:
                    if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                        os.remove(p)
                    elif os.path.isdir(p) and os.path.getmtime(p) < cutoff:
                        shutil.rmtree(p, ignore_errors=True)
                except Exception:
                    pass
        time.sleep(3600)

threading.Thread(target=_janitor, daemon=True).start()

@app.get("/")
async def root():
    return {"message": "PTSEL Clipper is live 🚀"}

# ---------------------------
#  Chunked upload endpoints
# ---------------------------

@app.post("/upload_chunk")
async def upload_chunk(
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),   # any unique string (we'll use filename on the client)
    index: int = Form(...),       # 0-based chunk index
    total: int = Form(...),       # total number of chunks
):
    """
    Save chunk i for upload_id into /chunks/<upload_id>/<i>.part
    """
    folder = os.path.join(CHUNKS_DIR, upload_id)
    os.makedirs(folder, exist_ok=True)
    part_path = os.path.join(folder, f"{index}.part")
    try:
        with open(part_path, "wb") as f:
            shutil.copyfileobj(chunk.file, f)
    finally:
        await chunk.close()

    return {"ok": True, "received": index, "total": total}

@app.post("/merge_and_trim")
async def merge_and_trim(
    background_tasks: BackgroundTasks,
    upload_id: str = Form(...),   # same as used during chunking
    original_name: str = Form(...),
    start_time: str = Form(...),  # "HH:MM:SS" or "MM:SS"
    end_time: str = Form(...),
):
    """
    Merge all chunks for upload_id, then run ffmpeg in the background to create a clip.
    Returns a predictable download URL which the frontend can poll until ready.
    """
    # 1) Merge chunks -> merged_path
    chunks_folder = os.path.join(CHUNKS_DIR, upload_id)
    if not os.path.isdir(chunks_folder):
        raise HTTPException(status_code=400, detail="Chunks not found for this upload_id.")

    # ensure chunks are all present
    parts = [f for f in os.listdir(chunks_folder) if f.endswith(".part")]
    if not parts:
        raise HTTPException(status_code=400, detail="No chunks to merge.")

    # merged original
    # use mp4 extension for ffmpeg convenience even if original was webm; ffmpeg detects by content
    merged_path = os.path.join(UPLOADS_DIR, f"{int(time.time())}_{os.path.basename(original_name)}")
    with open(merged_path, "wb") as merged:
        for part_name in sorted(parts, key=lambda x: int(x.split(".")[0])):
            with open(os.path.join(chunks_folder, part_name), "rb") as p:
                shutil.copyfileobj(p, merged)

    # 2) Prepare output (predictable) and background ffmpeg job
    stamp = int(time.time())
    out_name = f"clip_{stamp}.mp4"
    out_path = os.path.join(CLIPS_DIR, out_name)

    def _run_ffmpeg():
        # fast, robust settings (ultrafast + reasonable quality)
        cmd = [
            "ffmpeg", "-y",
            "-ss", start_time,
            "-to", end_time,
            "-i", merged_path,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "26",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            out_path
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except Exception as e:
            # leave a small marker file to indicate failure if needed
            with open(out_path + ".error.txt", "w") as f:
                f.write(str(e))

    background_tasks.add_task(_run_ffmpeg)

    return JSONResponse({
        "status": "processing",
        "download_url": f"/clips/{out_name}"
    })

# ---------------------------
#  Download serving
# ---------------------------

@app.get("/clips/{filename}")
async def get_clip(filename: str):
    path = os.path.join(CLIPS_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path, media_type="video/mp4", filename=filename)
    # return 404 with a simple JSON so the frontend can keep polling
    raise HTTPException(status_code=404, detail="Clip not ready yet")
