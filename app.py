# app.py
import os
import re
import asyncio
import tempfile
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# subtitle-first library
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

# downloader
import yt_dlp

# openai (for whisper fallback)
import openai

# Create app
app = FastAPI(title="Clipper API - Transcribe fallback")

# CORS so your frontend can talk
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Helper: extract YouTube ID from many URL forms
def extract_video_id(url: str) -> str | None:
    if not url:
        return None
    # handle short youtu.be and full URLs
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?\/]|$)",  # v=ID or /ID
        r"youtu\.be\/([0-9A-Za-z_-]{11})",  # youtu.be/ID
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


# Attempt to fetch YouTube transcript (subtitles) first
def fetch_subtitles(video_id: str, languages=("en", "en-US", "en-GB", "auto")) -> str:
    """
    Try to get a human/auto transcript from youtube_transcript_api.
    Returns a joined text string.
    Raises exceptions on failure.
    """
    # list_transcripts + find_transcript chain handles many versions
    transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
    # Use preferred languages order
    try:
        # find_transcript expects a list of language codes which it uses in order
        transcript = transcripts.find_transcript(list(languages))
    except Exception:
        # try to pick the default (first) transcript if find_transcript doesn't work
        # this may raise NoTranscriptFound if none exist
        transcript = transcripts.find_manually_created_transcript([t.language_code for t in transcripts])
    # fetch gives list of segments with 'text'
    entries = transcript.fetch()
    text = " ".join([e["text"] for e in entries])
    return text


# Download audio with yt_dlp into a temporary file and return the filepath
def download_audio_to_file(video_url: str) -> str:
    """
    Downloads best audio for the video_url into a temporary file (mp3/m4a) and returns its path.
    Caller is responsible for deleting the file.
    """
    tmp_dir = tempfile.mkdtemp(prefix="clipper_audio_")
    out_template = os.path.join(tmp_dir, "%(id)s.%(ext)s")

    ytdl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # extract audio might require ffmpeg/avconv to convert
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    with yt_dlp.YoutubeDL(ytdl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        # after postprocessing, the file path is:
        file_ext = "mp3"
        file_name = f"{info['id']}.{file_ext}"
        filepath = os.path.join(tmp_dir, file_name)
        if not os.path.exists(filepath):
            # if postprocessing changed name, try to find any file in tmp_dir
            candidates = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir)]
            if candidates:
                filepath = candidates[0]
            else:
                raise FileNotFoundError("Audio file not found after yt-dlp download.")


