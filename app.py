from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import yt_dlp
import openai
import tempfile
import subprocess

# Initialize FastAPI app
app = FastAPI()

# Allow frontend connection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Use your OpenAI API key from environment
openai.api_key = os.getenv("OPENAI_API_KEY")

# Request model
class VideoRequest(BaseModel):
    url: str

@app.get("/")
def root():
    return {"message": "Clipper AI backend is running!"}

@app.post("/transcribe")
async def transcribe_video(req: VideoRequest):
    video_url = req.url
    try:
        # 1️⃣ Download YouTube audio
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.mp3")

            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": audio_path,
                "quiet": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            if not os.path.exists(audio_path):
                raise FileNotFoundError("Audio file not found after download.")

            # 2️⃣ Transcribe using OpenAI Whisper
            with open(audio_path, "rb") as audio_file:
                transcript = openai.audio.transcriptions.create(
                    model="gpt-4o-mini-transcribe",
                    file=audio_file
                )

            return {"status": "success", "transcript": transcript.text}

    except Exception as e:
        return {"status": "error", "message": str(e)}
