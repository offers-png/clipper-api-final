from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip
import uuid
import os

app = FastAPI()

@app.post("/clip")
async def clip(video_file: UploadFile = File(...)):
    # Save uploaded file to /tmp
    input_path = f"/tmp/{uuid.uuid4()}_{video_file.filename}"
    with open(input_path, "wb") as f:
        f.write(await video_file.read())

    # Load video
    clip = VideoFileClip(input_path)

    # Create text watermark
    txt_clip = TextClip("@ClippedBySal", fontsize=50, color='white', stroke_color='black', stroke_width=2)
    txt_clip = txt_clip.set_duration(clip.duration).set_position(("center","bottom"))

    # Combine
    final = CompositeVideoClip([clip, txt_clip])

    # Output file
    output_path = f"/tmp/{uuid.uuid4()}_watermarked.mp4"
    final.write_videofile(output_path, codec="libx264", audio_codec="aac")

    # Return file for download
    return FileResponse(output_path, filename="watermarked.mp4")
