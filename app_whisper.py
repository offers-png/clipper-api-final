import os
import openai
import subprocess
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

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

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

openai.api_key = os.getenv("OPENAI_API_KEY")

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    try:
        # Save upload
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        # Convert to wav (mono 16kHz)
        wav_path = os.path.splitext(input_path)[0] + ".wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", wav_path
        ], check=True)

        # Call OpenAI Whisper
        with open(wav_path, "rb") as audio_file:
            transcript = openai.Audio.transcribe(
                model="whisper-1",
                file=audio_file
            )

        # DEBUG: print the raw response
        print("🧩 RAW Whisper Response:", transcript)

        text = transcript.get("text", "").strip()
        if not text:
            text = "(no speech detected — check audio clarity or file format)"

        return {"text": text}

    except subprocess.CalledProcessError:
        return JSONResponse({"error": "Audio conversion failed (ffmpeg error)"}, status_code=500)
    except Exception as e:
        print("❌ Whisper Error:", e)
        return JSONResponse({"error": str(e)}, status_code=500)
