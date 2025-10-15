from youtube_transcript_api import YouTubeTranscriptApi as yta

@app.post("/transcribe")
async def transcribe(payload: dict):
    video_url = payload.get("url") or payload.get("video_url")
    if not video_url:
        return JSONResponse(status_code=400, content={"error": "Missing video URL"})

    import re
    match = re.search(r"(?:v=|be/)([a-zA-Z0-9_-]{11})", video_url)
    if not match:
        return JSONResponse(status_code=400, content={"error": "Invalid YouTube URL"})
    video_id = match.group(1)

    try:
        # ✅ Correct API method name (current version)
        transcript = yta.list_transcripts(video_id).find_transcript(['en']).fetch()
        full_text = " ".join([entry["text"] for entry in transcript])

        return {
            "ok": True,
            "url": video_url,
            "transcript": full_text[:5000]
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
