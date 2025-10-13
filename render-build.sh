#!/usr/bin/env bash
set -o errexit  # Exit on error

echo "Installing FFmpeg..."
apt-get update && apt-get install -y ffmpeg

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Build completed successfully!"
