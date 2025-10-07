from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip
import uuid, os

app = FastAPI()

@app.post("/clip")
async def clip(video_file: UploadFile = File(...)):
    # Save upload
    input_path = f"/tmp/{uuid.uuid4()}_{video_file.filename}"
    with open(input_path, "wb") as f:
        f.write(await video_file.read())

    clip = VideoFileClip(input_path)

    # Watermark
    txt_clip = TextClip("@ClippedBySal", fontsize=50, color="white", stroke_color="black", stroke_width=2)
    txt_clip = txt_clip.set_duration(clip.duration).set_position(("center","bottom"))

    final = CompositeVideoClip([clip, txt_clip])
    output_name = f"{uuid.uuid4()}_watermarked.mp4"
    output_path = f"/tmp/{output_name}"
    final.write_videofile(output_path, codec="libx264", audio_codec="aac")

    # URL back to your service (your Render app URL)
    file_url = f"https://clipper-api-final.onrender.com/files/{output_name}"
    return {"download_url": file_url}

@app.get("/files/{filename}")
async def serve_file(filename: str):
    return FileResponse(f"/tmp/{filename}", filename=filename)

