# ==== Base image ====
FROM python:3.11-slim

# ==== Install system dependencies ====
RUN apt-get update && apt-get install -y ffmpeg streamlink && rm -rf /var/lib/apt/lists/*

# ==== Set working directory ====
WORKDIR /app

# ==== Copy all project files ====
COPY . .

# ==== Install Python dependencies ====
RUN pip install -U pip
RUN pip install -U fastapi uvicorn yt-dlp
RUN yt-dlp -U

# ==== Expose port ====
EXPOSE 10000

# ==== Start server ====
CMD ["uvicorn", "app_link:app", "--host", "0.0.0.0", "--port", "10000"]
