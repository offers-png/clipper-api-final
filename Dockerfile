# Use a base image with full codec support
FROM python:3.11-bullseye

ENV DEBIAN_FRONTEND=noninteractive

# ✅ Install full FFmpeg with all codecs (webm/opus/mp4/aac etc.)
RUN apt-get update && \
    apt-get install -y ffmpeg libavcodec-extra && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# ✅ Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 10000

# ✅ Start FastAPI app
CMD ["uvicorn", "app_whisper:app", "--host", "0.0.0.0", "--port", "10000"]
