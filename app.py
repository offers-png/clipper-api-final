from fastapi import FastAPI, UploadFile, File
import shutil
import os

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Server is alive!"}

@app.post("/clip")
async def clip(video_file: UploadFile = File(...)):
    # Save uploaded file
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, video_file.filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(video_file.file, buffer)

    # Respond with saved filename (replace with watermarking later)
    return {"filename": video_file.filename}
