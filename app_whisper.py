import os
import tempfile
import subprocess
import requests
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

# ✅ Initialize FastAPI
app = FastAPI()
client = OpenAI()

# ✅ Allow frontend + backend + localhost
origins = [
    "https://ptsel-frontend.onrender.com",
    "https://clipper-api-final-1.onrender.com",
    "http://localhost:5173"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "✅ Clipper AI Whisper API is live!"}

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(None), url: str = Form(None)):
    try:
        tmp_path = None
        audio_path = None

        # ✅ Ensure /tmp directory exists (Render safe)
        os.makedirs("/tmp", exist_ok=True)

        # ✅ Save uploaded file
        if file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm", dir="/tmp") as tmp:
                tmp.write(await file.read())
                tmp_path = tmp.name

        # ✅ OR download from URL
        elif url:
            response = requests.get(url, stream=True, timeout=60)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm", dir="/tmp") as tmp:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    tmp.write(chunk)
                tmp_path = tmp.name

        else:
            return JSONResponse({"error": "No file or URL provided."}, status_code=400)

        # ✅ Convert video/audio → .mp3
        audio_path = tmp_path.rsplit(".", 1)[0] + ".mp3"
        convert_cmd = [
            "ffmpeg", "-y", "-i", tmp_path, "-vn",
            "-acodec", "libmp3lame", "-ar", "44100", "-ac", "2", audio_path
        ]
        result = subprocess.run(convert_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Log FFmpeg stderr for debugging
        if result.returncode != 0:
            print("❌ FFmpeg stderr:", result.stderr.decode())
            raise Exception("FFmpeg failed to create audio file")

        # ✅ Send the converted audio to Whisper
        with open(audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )

        # ✅ Clean up temporary files
        for path in [tmp_path, audio_path]:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

        # ✅ Return transcript text
        text_output = transcript.strip() if transcript else ""
        if not text_output:
            return JSONResponse(
                {"text": "(no text found — maybe silent or unreadable audio)"}
            )

        return JSONResponse({"text": text_output})

    except Exception as e:
        print(f"❌ Error during transcription: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
