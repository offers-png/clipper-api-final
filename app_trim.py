import os
import shutil
import asyncio
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# ✅ CORS
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

# ✅ Upload folder
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ✅ Auto-cleanup every 3 days
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"Cleanup failed for {path}: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(asyncio.to_thread(auto_cleanup))

@app.get("/")
def home():
    return {"status": "PTSEL Clipper running and optimized!"}

@app.post("/clip")
async def clip_video(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    try:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # ✅ Trim inputs
        start, end = start.strip(), end.strip()
        if not start or not end:
            return JSONResponse({"error": "Start or end time missing"}, status_code=400)

        output_filename = f"trimmed_{file.filename}"
        output_path = os.path.join(UPLOAD_DIR, output_filename)

        # ✅ Direct stream copy (super fast, low CPU)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", start,
            "-to", end,
            "-i", file_path,
            "-c", "copy",
            "-y", output_path
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0:
            return JSONResponse({"error": f"FFmpeg failed: {result.stderr}"}, status_code=500)

        if not os.path.exists(output_path):
            return JSONResponse({"error": "Output file not created"}, status_code=500)

        return FileResponse(output_path, filename=output_filename)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
