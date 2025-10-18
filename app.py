import os
 import shutil
 import asyncio
 import subprocess
 from datetime import datetime, timedelta
 from fastapi import FastAPI, UploadFile, File, Form, Request
 from fastapi.responses import FileResponse, JSONResponse
 from fastapi.middleware.cors import CORSMiddleware
 app = FastAPI()
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
 UPLOAD_DIR = "/data/uploads"
 os.makedirs(UPLOAD_DIR, exist_ok=True)
 def auto_cleanup():
    now = datetime.now()
    for root, _, files in os.walk(UPLOAD_DIR):
        for file in files:
            path = os.path.join(root, file)
            if os.path.getmtime(path) < (now - timedelta(days=3)).timestamp():
                os.remove(path)
 @app.get("/")
 def home():
    return {"status": "PTSEL Clipper running and optimized!"}
 @app.post("/clip")
 async def clip_video(
    file: UploadFile = File(...),
    start: str = Form(...),
    end: str = Form(...),
 ):
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    if os.path.isdir(file_path):
        shutil.rmtree(file_path)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    start = start.strip()
    end = end.strip()
    if not start or not end:
        return JSONResponse({"error": "Start or end time missing"}, status_code=400)
    output_filename = f"trimmed_{file.filename}"
    output_path = os.path.join(UPLOAD_DIR, output_filename)
    cmd = [
        "ffmpeg",
        "-ss", start,
        "-to", end,
        "-i", file_path,
        "-c", "copy",
        output_path,
        "-y"
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            return JSONResponse({"error": f"FFmpeg failed: {result.stderr}"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return FileResponse(output_path, filename=output_filename)
