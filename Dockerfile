# =========================
# PTSEL Clipper Dockerfile
# =========================

# Use a slim Python image
FROM python:3.11-slim

# Install FFmpeg and yt-dlp for video processing
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    pip install yt-dlp && \
    rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy everything into the container
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port Render expects
ENV PORT=10000

# Run the FastAPI app with Uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]
