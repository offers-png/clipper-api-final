# ✅ Use full Debian image to support webm/opus/mp4 audio
FROM python:3.11-slim-bullseye

ENV DEBIAN_FRONTEND=noninteractive

# ✅ Install FFmpeg with all codecs (and OpenAI deps)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libavcodec-extra \
        libavformat58 \
        libavutil56 \
        libswresample3 \
        libswscale5 \
        libavfilter7 \
        wget \
        curl \
        && apt-get clean && rm -rf /var/lib/apt/lists/*

# ✅ Create app folder
WORKDIR /app

# ✅ Copy project files
COPY . .

# ✅ Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# ✅ Expose port
EXPOSE 10000

# ✅ Start unified FastAPI app
CMD ["uvicorn", "app_full:app", "--host", "0.0.0.0", "--port", "10000"]
