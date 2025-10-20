from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from openai import OpenAI
import os
import tempfile

app = FastAPI()
client = OpenAI()

@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(None),
    url: str = Form(None)
):
    try:
        # If a file is uploaded
        if file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp.write(await file.read())
                tmp_path = tmp.name
            with open(tmp_path, "rb") as audio:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio,
                    response_format="text"
                )
            os.remove(tmp_path)
            return JSONResponse({"text": transcript.strip()})

        # If a URL is provided instead
        elif url:
            import requests
            response = requests.get(url)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as audio:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio,
                    response_format="text"
                )
            os.remove(tmp_path)
            return JSONResponse({"text": transcript.strip()})

        else:
            return JSONResponse({"error": "No file or URL provided."}, status_code=400)

    except Exception as e:
        print(f"❌ Error during transcription: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
