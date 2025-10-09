from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from moviepy.editor import VideoFileClip
import os
import traceback

app = FastAPI()

# === SETTINGS ===
UPLOAD_DIR = "uploads"
MAX_FILE_SIZE_MB = 50

os.makedirs(UPLOAD_DIR, exist_ok=True)


def validate_file_size(file):
    file.file.seek(0, 2)  # go to end of file
    size_mb = file.file.tell() / (1024 * 1024)
    file.file.seek(0)  # reset pointer
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(status_code=413, detail="File too large. Max 50 MB allowed.")


@app.post("/clip")
async def make_clip(
    video_file: UploadFile = File(...),
    start: float = 0,
    end: float = 5
):
    try:
        # Validate file size
        validate_file_size(video_file)

        # Save uploaded file
        input_path = os.path.join(UPLOAD_DIR, video_file.filename)
        with open(input_path, "wb") as f:
            f.write(await video_file.read())

        # Prepare output filename
        name, ext = os.path.splitext(video_file.filename)
        output_path = os.path.join(UPLOAD_DIR, f"{name}_clip.mp4")

        # Process the video
        with VideoFileClip(input_path) as video:
            new_clip = video.subclip(start, end)
            new_clip.write_videofile(output_path, codec="libx264", audio_codec="aac")

        return {"message": "✅ Clip created successfully!", "output": f"/files/{os.path.basename(output_path)}"}

    except Exception as e:
        print("❌ ERROR:", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files/{filename}")
async def serve_file(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(file_path)

