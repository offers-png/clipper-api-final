from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # allow all domains for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import ffmpeg
import uuid
import os

app = FastAPI(title="PTSEL Clipper API")

# Enable CORS for your frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
OUTPUT_DIR = "clips"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

def parse_time(t: str) -> float:
    """Convert flexible time formats into seconds (supports 5, 00:05, 0:05, 00:00:05, etc.)"""
    try:
        if not t:
            raise ValueError("Empty time string")
        t = str(t).strip().replace("string", "").replace("'", "").replace('"', "")
        if ":" not in t:
            return float(t)
        parts = [float(p) for p in t.split(":") if p.strip() != ""]
        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid time format: {t}")

@app.get("/")
def home():
    return {"message": "PTSEL Clipper API is running ✅"}

@app.post("/trim")
async def trim_video(
    file: UploadFile = File(...),
    url: str = Form(""),
    start: str = Form("00:00"),
    end: str = Form("00:10"),
):
    try:
        input_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{file.filename}")
        output_id = str(uuid.uuid4())
        output_path = os.path.join(OUTPUT_DIR, f"{output_id}.mp4")

        with open(input_path, "wb") as f:
            f.write(await file.read())

        start_seconds = parse_time(start)
        end_seconds = parse_time(end)

        if end_seconds <= start_seconds:
            raise HTTPException(status_code=400, detail="End time must be after start time")

        duration = end_seconds - start_seconds

        ffmpeg.input(input_path, ss=start_seconds, t=duration).output(
            output_path, vcodec="libx264", acodec="aac", strict="experimental"
        ).run(overwrite_output=True)

        size_bytes = os.path.getsize(output_path)
        download_url = f"/download/{output_id}"
        return JSONResponse({"ok": True, "download_url": download_url, "size_bytes": size_bytes})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{clip_id}")
def download_clip(clip_id: str):
    path = os.path.join(OUTPUT_DIR, f"{clip_id}.mp4")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Clip not found.")
    return FileResponse(path, media_type="video/mp4", filename=f"{clip_id}.mp4")

