import openai
import os
from fastapi import FastAPI, UploadFile, File, Form
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
        # save uploaded file
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        # --- real whisper API call ---
        with open(input_path, "rb") as audio:
            transcript = openai.Audio.transcriptions.create(
                model="whisper-1",
                file=audio
            )

        text = transcript.text if hasattr(transcript, "text") else "(no text found)"
        return {"text": text}

    except Exception as e:
        print("❌ Whisper error:", e)
        return JSONResponse({"error": str(e)}, status_code=500)
