from fastapi import FastAPI, UploadFile, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip
import uuid, os, shutil

app = FastAPI()

# ✅ storage paths
CLIP_DIR = "clips"
os.makedirs(CLIP_DIR, exist_ok=True)
JOBS = {}

def process_clip(video_path, start, end, output_path, job_id):
    try:
        clip = VideoFileClip(video_path).subclip(float(start), float(end))
        watermark = TextClip(
            "@ClippedBySal", fontsize=40, color="white", stroke_color="black"
        ).set_pos(("right", "bottom")).set_duration(clip.duration)
        final = CompositeVideoClip([clip, watermark])
        final.write_videofile(output_path, codec="libx264", audio_codec="aac")
        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["file_path"] = output_path
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
    finally:
        if os.path.exists(video_path):
            os.remove(video_path)

@app.post("/clip")
async def create_clip(background_tasks: BackgroundTasks, file: UploadFile, start: float = Form(0), end: float = Form(10)):
    # ✅ save uploaded file
    temp_path = os.path.join(CLIP_DIR, f"temp_{uuid.uuid4()}.mp4")
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # ✅ job setup
    job_id = str(uuid.uuid4())
    output_path = os.path.join(CLIP_DIR, f"{job_id}.mp4")
    JOBS[job_id] = {"status": "processing"}

    background_tasks.add_task(process_clip, temp_path, start, end, output_path, job_id)

    return {"job_id": job_id, "status": "processing"}

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    if job["status"] == "done":
        file_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/download/{job_id}"
        return {"status": "done", "download_url": file_url}
    return job

@app.get("/download/{job_id}")
async def download_clip(job_id: str):
    job = JOBS.get(job_id)
    if not job or "file_path" not in job:
        return JSONResponse({"error": "Clip not ready"}, status_code=404)
    return FileResponse(job["file_path"], media_type="video/mp4", filename=f"{job_id}.mp4")

@app.get("/")
def root():
    return {"message": "Clipped by Sal API is running"}
