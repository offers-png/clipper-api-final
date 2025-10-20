# Use a lightweight Python base image
FROM python:3.11-slim

# Prevent interactive prompts during install
ENV DEBIAN_FRONTEND=noninteractive

# Install ffmpeg and dependencies for audio/video handling
RUN apt-get update && apt-get install -y ffmpeg && apt-get clean

# Set working directory
WORKDIR /app

# Copy your app files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port Render expects
EXPOSE 10000

# Start the FastAPI app with uvicorn
CMD ["uvicorn", "app_whisper:app", "--host", "0.0.0.0", "--port", "10000"]
