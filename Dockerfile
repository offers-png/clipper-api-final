# Use lightweight Python base
FROM python:3.11-slim

# Install FFmpeg and dependencies
RUN apt-get update && \
    apt-get install -y ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
WORKDIR /app
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose Render port
ENV PORT=10000
EXPOSE 10000

# Start server
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]
