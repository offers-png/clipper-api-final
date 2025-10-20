import os, subprocess, requests
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

origins = ["https://ptsel-frontend.onrender.com", "http://localhost:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "/data/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def run_ffmpeg(input_path, start, end, output_path):
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", start,
        "-to", end,
        "-i", input_path,
        "-c", "copy",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
        input_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

        # --- YouTube links handled via proxy ---
        if "youtube.com" in url or "youtu.be" in url:
            try:
                print("Fetching YouTube video through proxy API...")
                proxy_api = f"https://youapi.matsurihi.me/api/youtube?url={url}"
                res = requests.get(proxy_api, timeout=30)
                data = res.json()

                video_url = data.get("video_url") or data.get("url")
                if not video_url:
                    raise ValueError("Proxy API did not return a valid video URL.")

                with requests.get(video_url, stream=True) as r:
                    r.raise_for_status()
                    with open(input_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)

            except Exception as e:
                return JSONResponse({"error": f"Proxy YouTube fetch failed: {str(e)}"}, status_code=500)

        # --- Direct MP4 or video file links ---
        else:
            with requests.get(url, stream=True) as r:
                r.raise_for_status()
                with open(input_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

        # --- Trim video ---
        run_ffmpeg(input_path, start, end, output_path)

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=f"trimmed_{video_id}.mp4"
        )

    except Exception as e:
        return JSONResponse({"error": f"Server error: {str(e)}"}, status_code=500)
