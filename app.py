from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from youtube_transcript_api import YouTubeTranscriptApi
import re

# ✅ Create app first
app = FastAPI()

# ✅ Allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/transcribe")
async def transcribe(payload: dict):
    try:
        # ✅ Extract video URL
        video_url = payload.get("url") or payload.get("video_url")
        if not video_url:
            return JSONResponse(status_code=400, content={"error": "Missing video URL"})

        # ✅ Extract the YouTube video ID
        match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", video_url)
        if not match:
            return JSONResponse(status_code=400, content={"error": "Invalid YouTube URL"})
        video_id = match.group(1)

        # ✅ Try to fetch transcript (all available languages and auto captions)
        transcript = YouTubeTranscriptApi.list_transcripts(video_id).find_transcript(
            ['en', 'en-US', 'en-GB', 'auto', 'es', 'fr', 'de', 'ar']
        ).fetch()

        full_text = " ".join([entry["text"] for entry in transcript])

        return {
            "ok": True,
            "url": video_url,
            "transcript": full_text[:5000]
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/")
def home():
    return {"message": "Clipper AI backend is running!"}
