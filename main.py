from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os
import uuid
from urllib.parse import urlparse
from pathlib import Path
import ipaddress
import socket

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
DOWNLOAD_DIR_PATH = Path(DOWNLOAD_DIR).resolve()

def is_ip_safe(ip):
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return False
    return True

def is_safe_url(url: str) -> bool:
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
        return False

    try:
        ip = ipaddress.ip_address(hostname)
        if not is_ip_safe(ip):
            return False
    except ValueError:
        try:
            resolved_ips = socket.getaddrinfo(hostname, None)
            for result in resolved_ips:
                ip = ipaddress.ip_address(result[4][0])
                if not is_ip_safe(ip):
                    return False
        except:
            return False

    return True

@app.get("/")
def root():
    return {"ok": True, "service": "ClipForge AI Backend", "version": "3.1.0"}

@app.get("/fetch")
def fetch(url: str = Query(...)):
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    try:
        file_id = uuid.uuid4().hex
        filename = f"{file_id}.mp4"
        output_path = os.path.join(DOWNLOAD_DIR, filename)

        ydl_opts = {
            "outtmpl": output_path,
            "format": "best[ext=mp4]/best",
            "quiet": True,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        return {
            "success": True,
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "filename": filename,
            "download_url": f"/download/{filename}"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{filename}")
def download(filename: str):
    path = DOWNLOAD_DIR_PATH / filename

    if not path.exists():
        raise HTTPException(404, "File not found")

    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=filename,
        headers={"Cache-Control": "no-cache"}
    )

app.mount("/static", StaticFiles(directory="static"), name="static")
