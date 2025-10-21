# ✅ Full Debian base (includes essential codecs)
FROM debian:bullseye

ENV DEBIAN_FRONTEND=noninteractive

# ✅ Install Python and FFmpeg with all audio/video codecs
RUN apt-get update && \
    apt-get install -y python3 python3-pip ffmpeg libavcodec-extra && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ✅ Set up workdir
WORKDIR /app
COPY . .

# ✅ Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 10000

# ✅ Start unified backend (Whisper + Clipper)
CMD ["uvicorn", "app_full:app", "--host", "0.0.0.0", "--port", "10000"]
