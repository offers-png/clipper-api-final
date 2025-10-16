#!/usr/bin/env bash
# render-build.sh

# Make the file fail fast if anything goes wrong
set -o errexit

# Install ffmpeg (needed for clipping)
apt-get update -y
apt-get install -y ffmpeg

# Install Python packages
pip install --upgrade pip
pip install -r requirements.txt
