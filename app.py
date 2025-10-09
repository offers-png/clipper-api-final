from fastapi import FastAPI, File, UploadFile, Form
from moviepy.editor import VideoFileClip
import uuid, os

app = FastAPI()

JOBS = {}  # In-memory job tracking
OUTPUT_DIR = "files"
os.makedirs(OUTPUT_DIR, exist_ok=True)

@app.get("/")
def root():
    return {"message": "Clipped by Sal API is running"}

@app.post("/clip")
async def make_clip(file: UploadFile = File(...), start: float = Form(...), end: float = Form(...)):
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "processing"}
    input_path = f"{OUTPUT_DIR}/{file.filename}"
    output_path = f"{OUTPUT_DIR}/{file.filename.split('.')[0]}_clip.mp4"

    try:
        # Save uploaded video
        with open(input_path, "wb") as f:
            f.write(await file.read())

        # Clip it
        with VideoFileClip(input_path) as video:
            new_clip = video.subclip(start, end)
            new_clip.write_videofile(output_path, codec="libx264", audio_codec="aac")

        JOBS[job_id] = {
            "status": "done",
            "output": output_path
        }

        return {"job_id": job_id, "status": "processing"}

    except Exception as e:
        JOBS[job_id] = {"status": "failed", "error": str(e)}
        return {"error": str(e)}

@app.get("/status/{job_id}")
def check_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return {"error": "Job not found"}
    if job["status"] == "done":
        return {"status": "done", "download_url": f"/download/{job_id}"}
    return {"status": job["status"]}

@app.get("/download/{job_id}")
def download(job_id: str):
    job = JOBS.get(job_id)
    if not job or "output" not in job:
        return {"error": "Job not ready"}
    return {
        "download": f"https://clipper-api-final.onrender.com/{job['output']}"
    }
