@app.post("/transcribe")
async def transcribe(payload: dict):
    try:
        import requests
        import re
        import json

        video_url = payload.get("url") or payload.get("video_url")
        if not video_url:
            return JSONResponse(status_code=400, content={"error": "Missing video URL"})

        match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", video_url)
        if not match:
            return JSONResponse(status_code=400, content={"error": "Invalid YouTube URL"})
        video_id = match.group(1)

        # ✅ Backup caption source (unofficial)
        captions_url = f"https://youtubetranscriptapi.vyom.tech/?video_id={video_id}"
        response = requests.get(captions_url)

        if response.status_code != 200:
            return JSONResponse(status_code=404, content={"error": "Could not fetch captions. Subtitles might be disabled."})

        data = response.json()
        if "transcript" not in data:
            return JSONResponse(status_code=404, content={"error": "No transcript found for this video."})

        # ✅ Combine transcript text
        full_text = " ".join([entry["text"] for entry in data["transcript"]])
        if not full_text.strip():
            return JSONResponse(status_code=404, content={"error": "Transcript is empty or unavailable."})

        return {
            "ok": True,
            "url": video_url,
            "transcript": full_text[:5000]
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
