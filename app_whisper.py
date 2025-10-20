import openai
import os
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
async def clip_whisper(file: UploadFile = File(...)):
    try:
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        # 🔥 Force simple audio conversion for video files
        wav_path = input_path + ".wav"
        os.system(f"ffmpeg -y -i '{input_path}' -ar 16000 -ac 1 '{wav_path}'")

        # --- real Whisper API call ---
        with open(wav_path, "rb") as audio_file:
            transcript = openai.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )

        text = transcript.text if hasattr(transcript, "text") else "(no text found)"
        return {"text": text}

    except Exception as e:
        print("❌ Whisper error:", e)
        return JSONResponse({"error": str(e)}, status_code=500)
