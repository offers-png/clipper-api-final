# ==== Base image ====
FROM python:3.11-slim

# ==== Install system packages ====
RUN apt-get update && apt-get install -y ffmpeg streamlink && rm -rf /var/lib/apt/lists/*

# ==== Working directory ====
WORKDIR /app

# ==== Copy project files ====
COPY . .

# ==== Install Python dependencies ====
RUN pip install -U pip
RUN pip install -U fastapi uvicorn yt-dlp
RUN yt-dlp -U  # keep extractors fresh

# ==== Expose app port ====
EXPOSE 10000

# ==== Start FastAPI server ====
CMD ["uvicorn", "app_link:app", "--host", "0.0.0.0", "--port", "10000"]
