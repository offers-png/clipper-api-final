import os
import subprocess
import openai
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import tempfile
import io

app = FastAPI()

# ✅ Allow your frontend
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

openai.api_key = os.getenv("OPENAI_API_KEY")
UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    try:
        # ✅ Save uploaded file
        input_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        # ✅ Convert to smaller audio WAV for Whisper
        wav_path = os.path.splitext(input_path)[0] + "_compressed.wav"
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vn",                     # no video
            "-acodec", "pcm_s16le",    # uncompressed WAV
            "-ar", "16000",            # 16kHz sample rate
            "-ac", "1",                # mono
            "-f", "wav",
            wav_path
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        # ✅ Stream big files to Whisper safely
        CHUNK_SIZE = 20 * 1024 * 1024  # 20MB chunks for large files
        file_size = os.path.getsize(wav_path)

        if file_size > 25 * 1024 * 1024:
            print(f"⚙️ Large file detected ({file_size/1e6:.1f} MB), streaming...")
            text_accumulator = ""
            with open(wav_path, "rb") as audio_file:
                while True:
                    chunk = audio_file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    audio_stream = io.BytesIO(chunk)
                    try:
                        result = openai.audio.transcriptions.create(
                            model="whisper-1",
                            file=audio_stream
                        )
                        chunk_text = getattr(result, "text", "").strip()
                        text_accumulator += f" {chunk_text}"
                    except Exception as err:
                        print("❌ Chunk failed:", err)
                        break
            text = text_accumulator.strip() or "(no text found)"
        else:
            # ✅ Normal smaller file
            with open(wav_path, "rb") as f:
                transcript = openai.audio.transcriptions.create(
                    model="whisper-1",
                    file=f
                )
            text = getattr(transcript, "text", "").strip() or "(no text found)"

        print("✅ Transcription finished.")
        return {"text": text}

    except subprocess.CalledProcessError as e:
        print("❌ ffmpeg error:", e)
        return JSONResponse({"error": "Audio conversion failed (ffmpeg issue)"}, status_code=500)
    except Exception as e:
        print("❌ Whisper error:", e)
        return JSONResponse({"error": str(e)}, status_code=500)
