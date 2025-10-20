import os
import tempfile
import subprocess
import requests
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

app = FastAPI()
client = OpenAI()

origins = ["https://ptsel-frontend.onrender.com", "http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(None), url: str = Form(None)):
    try:
        tmp_path = None
        audio_path = None

        # ✅ Save the uploaded or downloaded file
        if file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
                tmp.write(await file.read())
                tmp_path = tmp.name
        elif url:
            response = requests.get(url, stream=True)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    tmp.write(chunk)
                tmp_path = tmp.name
        else:
            return JSONResponse({"error": "No file or URL provided."}, status_code=400)

        # ✅ Convert to MP3 before sending to Whisper
        audio_path = tmp_path.replace(".webm", ".mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_path, "-vn", "-acodec", "mp3", audio_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # ✅ Send the converted file to Whisper
        with open(audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )

        os.remove(tmp_path)
        os.remove(audio_path)

        text_output = transcript.strip() if transcript else ""
        if not text_output:
            return JSONResponse({"text": "(no text found — maybe silent or unreadable audio)"} )

        return JSONResponse({"text": text_output})

    except Exception as e:
        print(f"❌ Error during transcription: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
