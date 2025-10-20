import os, subprocess
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def run_cmd(cmd):
    try:
        subprocess.run(cmd, check=True)
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False

# ---------- Root ----------
@app.get("/")
def home():
    return {"status": "Clipper API is live!"}

# ---------- YouTube / TikTok / URL ----------
@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        file_id = url.split("/")[-1].split("?")[0]
        input_path = os.path.join(UPLOAD_DIR, f"{file_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{file_id}.mp4")

        if not run_cmd(["yt-dlp", "-o", input_path, url]):
            return JSONResponse({"error": "❌ Unable to fetch that link. It may be private or DRM-protected."}, status_code=400)

        run_cmd(["ffmpeg", "-y", "-i", input_path, "-ss", start, "-to", end, "-c", "copy", output_path])
        return FileResponse(output_path, media_type="video/mp4", filename=f"trimmed_{file_id}.mp4")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ---------- File Upload ----------
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

        # Placeholder Whisper (for now just respond success)
        return {"status": "✅ Transcription complete (placeholder)"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
