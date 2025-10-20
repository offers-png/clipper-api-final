@app.post("/clip_link")
async def clip_link(url: str = Form(...), start: str = Form(...), end: str = Form(...)):
    try:
        video_id = url.split("v=")[-1] if "v=" in url else url.split("/")[-1]
        input_path = os.path.join(UPLOAD_DIR, f"{video_id}.mp4")
        output_path = os.path.join(UPLOAD_DIR, f"trimmed_{video_id}.mp4")

        # ✅ Download directly using yt_dlp
        ydl_opts = {
            "outtmpl": input_path,
            "format": "best[ext=mp4]/mp4",
            "quiet": True,
            "noplaylist": True,
            "nocheckcertificate": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # ✅ Trim with ffmpeg
        run_ffmpeg(input_path, start, end, output_path)

        # ✅ Return the trimmed file
        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=f"trimmed_{video_id}.mp4"
        )

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
