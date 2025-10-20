import os
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from openai import OpenAI
import tempfile

client = OpenAI()

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    try:
        # Save file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        print(f"✅ Received file: {tmp_path} ({os.path.getsize(tmp_path)/1_000_000:.2f} MB)")

        # Send to Whisper
        print("🎙️ Sending file to Whisper...")
        with open(tmp_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )

        text = transcript.strip() if transcript else "(no text found)"
        print(f"✅ Whisper returned: {text[:100]}...")

        # Clean up
        os.remove(tmp_path)

        return JSONResponse({"text": text})

    except Exception as e:
        print(f"❌ Whisper error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
