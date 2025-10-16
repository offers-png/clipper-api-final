from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import yt_dlp
import openai
import tempfile
import traceback

# Initialize FastAPI
app = FastAPI()

# Allow frontend connection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")

# Request model
class VideoRequest(BaseModel):
    url: str

@app.get("/")
def root():
    return {"message": "Clipper AI backend is running!"}

@app.post("/transcribe")
async def transcribe(request: Request):
    try:
        data = await request.json()
        video_url = data.get("url")

        if not video_url:
            return {"error": "Missing YouTube URL"}

        tempdir = tempfile.mkdtemp()
        outfile = os.path.join(tempdir, "audio.%(ext)s")

        # 🔐 Handle YouTube cookies file or ENV
        cookie_path = "/run/secrets/youtube.com_cookies.txt"
        if not os.path.exists(cookie_path):
            cookie_env = os.getenv("YOUTUBE_COOKIES")
            if cookie_env:
                cookie_path = os.path.join(tempfile.gettempdir(), "youtube_cookies.txt")
                with open(cookie_path, "w", encoding="utf-8") as f:
                    f.write(cookie_env)

        # 🎧 yt-dlp options
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

        # 🧠 Transcribe using OpenAI Whisper
        with open(audio_path, "rb") as f:
            transcript = openai.Audio.transcriptions.create(
                model="whisper-1",
                file=f
            )

        return {"success": True, "text": transcript.text}

    except yt_dlp.utils.DownloadError as e:
        return {"error": f"Transcription failed: YouTube blocked the request. Try re-uploading your cookies. Details: {str(e)[:150]}..."}

    except Exception as e:
        print(traceback.format_exc())
        return {"error": f"Unexpected server error: {str(e)}"}
