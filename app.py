from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/transcribe")
async def transcribe(payload: dict):
    try:
        video_url = payload.get("url") or payload.get("video_url")
        if not video_url:
            return JSONResponse(status_code=400, content={"error": "Missing video URL"})

        # ✅ Extract YouTube video ID
        match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", video_url)
        if not match:
            return JSONResponse(status_code=400, content={"error": "Invalid YouTube URL"})
        video_id = match.group(1)

        # ✅ Try multiple languages
        possible_langs = ['en', 'en-US', 'en-GB', 'auto']

        # ✅ Get available transcripts
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)

        # Find one available language
        transcript = None
        for lang in possible_langs:
            try:
                transcript = transcripts.find_transcript([lang]).fetch()
                break
            except:
                continue

        if not transcript:
            return JSONResponse(status_code=404, content={"error": "No transcript found for this video."})

        # ✅ Combine transcript text
        full_text = " ".join([entry["text"] for entry in transcript])
        if not full_text.strip():
            return JSONResponse(status_code=404, content={"error": "Transcript is empty or unavailable."})

        return {
            "ok": True,
            "url": video_url,
            "transcript": full_text[:5000]
        }

    except Exception as e:
        if "Subtitles are disabled" in str(e):
            return JSONResponse(status_code=404, content={"error": "Subtitles are disabled for this video."})
        elif "Could not retrieve" in str(e):
            return JSONResponse(status_code=404, content={"error": "Could not retrieve transcript. Video may not have subtitles."})
        else:
            return JSONResponse(status_code=500, content={"error": str(e)})
