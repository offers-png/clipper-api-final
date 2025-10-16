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
async def transcribe(request: Request):
    data = await request.json()
    video_url = data.get("url")

    if not video_url:
        return {"error": "Missing YouTube URL"}

    try:
        tempdir = tempfile.mkdtemp()
        outfile = os.path.join(tempdir, "audio.%(ext)s")

        cookie_path = "/run/secrets/youtube.com_cookies.txt"
        if not os.path.exists(cookie_path):
            cookie_path = "youtube.com_cookies.txt"

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outfile,
            "quiet": True,
            "noplaylist": True,
        }

        if os.path.exists(cookie_path):
            ydl_opts["cookiefile"] = cookie_path

        # Download audio
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            audio_path = ydl.prepare_filename(info)

        # Transcribe using OpenAI
        with open(audio_path, "rb") as f:
            transcript = openai.Audio.transcriptions.create(
                model="whisper-1",
                file=f
            )

        return {"success": True, "text": transcript.text}

    except Exception as e:
        return {"error": str(e)}

