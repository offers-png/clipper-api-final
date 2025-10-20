# Use lightweight Python image
FROM python:3.11-slim

# Install ffmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 10000
ENV PORT=10000

# Start the FastAPI app
CMD ["uvicorn", "app_link:app", "--host", "0.0.0.0", "--port", "10000"]
