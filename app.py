from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
import os, shutil, subprocess

app = FastAPI()

UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.get("/")
def home():
    return {"status": "PTSEL Clipper running!"}

# Upload each chunk
@app.post("/upload_chunk")
async def upload_chunk(
    chunk: UploadFile = File(...),
    filename: str = Form(...),
    index: int = Form(...),
    total: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...)
):
    temp_dir = os.path.join(UPLOAD_DIR, filename)
    os.makedirs(temp_dir, exist_ok=True)
    chunk_path = os.path.join(temp_dir, f"{index}.part")
    with open(chunk_path, "wb") as f:
        shutil.copyfileobj(chunk.file, f)
    return {"status": "ok", "chunk_index": index}

# Merge all chunks and trim
@app.post("/merge_chunks")
async def merge_chunks(request: Request):
    data = await request.json()
    filename = data["filename"]
    start = data["start_time"]
    end = data["end_time"]
    temp_dir = os.path.join(UPLOAD_DIR, filename)
    merged_path = os.path.join(UPLOAD_DIR, filename)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Merge all chunks into one video file
    with open(merged_path, "wb") as merged:
        for part in sorted(os.listdir(temp_dir), key=lambda x: int(x.split(".")[0])):
            with open(os.path.join(temp_dir, part), "rb") as f:
                shutil.copyfileobj(f, merged)

    # Trim the video with ffmpeg
    output_path = os.path.join(UPLOAD_DIR, f"trimmed_{filename}")
    try:
       subprocess.run(
    [
        "ffmpeg", "-y",
        "-hide_banner",
        "-hwaccel", "auto",          # use hardware acceleration if available
        "-ss", start,
        "-to", end,
        "-accurate_seek",            # more precise cuts without full re-encode
        "-i", merged_path,
        "-c:v", "libx264",           # fast x264 encoding
        "-preset", "ultrafast",      # prioritize speed
        "-crf", "28",                # balance speed/quality
        "-c:a", "aac",               # faster audio encode
        "-b:a", "128k",
        output_path
    ],
    check=True
)

    except subprocess.CalledProcessError:
        return JSONResponse({"error": "Trimming failed"}, status_code=500)

    # Cleanup chunks
    shutil.rmtree(temp_dir, ignore_errors=True)
    return JSONResponse({"download_url": f"/download/{os.path.basename(output_path)}"})

# Download the final clip
@app.get("/download/{filename}")
async def download_file(filename: str):
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(path, filename=filename)

