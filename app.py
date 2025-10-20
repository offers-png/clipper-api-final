import os, subprocess
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Allow your frontend to connect
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

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def run_cmd(cmd):
    try:
        subprocess.run(cmd, check=True)
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False

@app.get("/")
def home():
    return {"status": "Clipper AI v1 is live!"}

# ---------- File Upload (Trimmer) ----------
@app.post("/clip_upload")
async def clip_upload(file: UploadFile = File(...), start: str = Form(...), end: str = Form(...)):
    try:
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{file.filename}")

        with open(input_path, "wb") as f:
            f.write(await file.read())

        run_cmd(["ffmpeg", "-y", "-i", input_path, "-ss", start, "-to", end, "-c", "copy", output_path])
        return FileResponse(output_path, media_type="video/mp4", filename=f"trimmed_{file.filename}")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ---------- Whisper Transcription ----------
@app.post("/clip_whisper")
async def clip_whisper(file: UploadFile = File(...)):
    try:
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        # Placeholder response for now
        return {"status": f"✅ Transcription complete for {file.filename} (placeholder)"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
