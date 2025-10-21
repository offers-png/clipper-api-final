# ✅ Use full Debian image to support webm/opus/mp4 audio
FROM python:3.11-bullseye

ENV DEBIAN_FRONTEND=noninteractive

# ✅ Install FFmpeg with all codecs
RUN apt-get update && \
    apt-get install -y ffmpeg libavcodec-extra && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ✅ Copy and install dependencies
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 10000

# ✅ Start combined app (both Whisper + Trim)
CMD ["uvicorn", "app_full:app", "--host", "0.0.0.0", "--port", "10000"]
