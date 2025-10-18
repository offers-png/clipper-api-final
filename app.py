import os
import shutil
import asyncio
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ✅ Create the FastAPI app FIRST
app = FastAPI()

# ✅ CORS (connect backend with frontend)
origins = [
    "https://ptsel-frontend.onrender.com",  # your Render frontend
    "http://localhost:5173",                # optional local testing
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Upload directory setup
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ✅ Auto cleanup every 3 days
def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                os.remove(path)

# ✅ Routes
@app.get("/")
def home():
    return {"status": "PTSEL Clipper running and optimized!"}

@app.post("/clip")
async def clip_video(
    file: UploadFile = File(...),
    start: str = Form(...),
    end: str = Form(...),
):
    # Clean up any existing folder named after the upload
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    if os.path.isdir(file_path):
        shutil.rmtree(file_path)  # delete folder if it exists

    # Save uploaded video safely
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # TODO: Add FFmpeg trimming logic here
    return {"status": f"✅ File '{file.filename}' uploaded successfully!", "start": start, "end": end}
