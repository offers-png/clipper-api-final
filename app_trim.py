import os, shutil, subprocess
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

origins = [
    "https://ptsel-frontend.onrender.com",
    "http://localhost:5173"
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

@app.post("/clip")
async def clip_video(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    try:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{file.filename}")

        cmd = [
            "ffmpeg", "-y",
            "-ss", start,
            "-to", end,
            "-i", file_path,
            "-c:v", "libx264",
            "-c:a", "aac",
            output_path
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0:
            # Decode the stderr bytes to string before returning
            err_msg = result.stderr.decode("utf-8", errors="ignore")
            print("FFMPEG ERROR:", err_msg)
            return JSONResponse({"error": err_msg}, status_code=500)

        if not os.path.exists(output_path):
            return JSONResponse({"error": "Output file not created."}, status_code=500)

        return FileResponse(output_path, filename=f"trimmed_{file.filename}")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
