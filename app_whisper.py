import os
import tempfile
import requests
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

app = FastAPI()
client = OpenAI()

origins = [
    "https://ptsel-frontend.onrender.com",
    "http://localhost:5173"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(None), url: str = Form(None)):
    """
    Handles both file uploads and URL-based audio/video transcription.
    Supports large files by streaming chunks to disk before Whisper processing.
    """
    try:
        tmp_path = None

        # Handle direct file upload
        if file:
            print(f"📥 Received file: {file.filename}")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                while chunk := file.file.read(1024 * 1024):  # read in 1MB chunks
                    tmp.write(chunk)
                tmp_path = tmp.name

        # Handle URL input
        elif url:
            print(f"🌐 Downloading from URL: {url}")
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    tmp.write(chunk)
                tmp_path = tmp.name

        # No file or URL
        else:
            return JSONResponse({"error": "No file or URL provided."}, status_code=400)

        # Log file size
        size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
        print(f"📦 Saved temp file: {tmp_path} ({size_mb:.2f} MB)")

        # Send to Whisper
        with open(tmp_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )

        print("🧾 TRANSCRIPT RESULT:", repr(transcript))  # DEBUG LOG

        os.remove(tmp_path)
        print("🧹 Temp file deleted successfully.")

        # Handle empty transcription gracefully
        text_output = transcript.strip()
        if not text_output:
            return JSONResponse({"text": "(no speech detected or empty audio)"} )

        return JSONResponse({"text": text_output})

    except Exception as e:
        print(f"❌ Error during transcription: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
