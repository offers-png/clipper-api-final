import os
import tempfile
import requests
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

app = FastAPI()
client = OpenAI()

# ✅ CORS setup so your frontend connects fine
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

# ✅ WHISPER endpoint (upload or URL)
@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(None),
    url: str = Form(None)
):
    try:
        # File upload
        if file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp.write(await file.read())
                tmp_path = tmp.name
            with open(tmp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )
            os.remove(tmp_path)
            return JSONResponse({"text": transcript.strip()})

        # URL upload
        elif url:
            response = requests.get(url)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )
            os.remove(tmp_path)
            return JSONResponse({"text": transcript.strip()})

        # Neither provided
        else:
            return JSONResponse({"error": "No file or URL provided."}, status_code=400)

    except Exception as e:
        print(f"❌ Error during transcription: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
