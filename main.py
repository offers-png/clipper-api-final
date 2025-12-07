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


def is_ip_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return False

    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ipv4 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
            if ipv4.is_private or ipv4.is_loopback or ipv4.is_link_local or ipv4.is_reserved:
                return False

        if ip.sixtofour:
            ipv4 = ip.sixtofour
            if ipv4.is_private or ipv4.is_loopback or ipv4.is_link_local or ipv4.is_reserved:
                return False

        if ip.teredo:
            server, client = ip.teredo
            if server.is_private or server.is_loopback or server.is_link_local or server.is_reserved:
                return False
            if client.is_private or client.is_loopback or client.is_link_local or client.is_reserved:
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
        except (socket.gaierror, ValueError):
            return False

    return True


@app.get("/")
def read_root():
    return FileResponse("static/index.html")


@app.get("/fetch")
def fetch(url: str = Query(...)):
    if not is_safe_url(url):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL. Only public HTTP and HTTPS URLs are supported."
        )

    try:
        filename = f"{uuid.uuid4().hex}.mp4"
        output_path = os.path.join(DOWNLOAD_DIR, filename)

        cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
        
        ydl_opts = {
            "outtmpl": output_path,
            "format": "best[ext=mp4]/best",
            "quiet": True,
            "noplaylist": True,
        }
        
        if os.path.exists(cookies_path):
            ydl_opts["cookiefile"] = cookies_path

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            actual_filename = None
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(filename.split('.')[0]):
                    actual_filename = f
                    break

            if not actual_filename:
                actual_filename = filename

            return JSONResponse({
                "success": True,
                "title": info.get("title", "Unknown"),
                "filename": actual_filename,
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "Unknown"),
                "download_url": f"/download/{actual_filename}"
            })
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=502,
                            detail=f"Failed to download video: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


@app.get("/download/{filename}")
def download_file(filename: str):
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = Path(DOWNLOAD_DIR) / filename
    resolved_path = file_path.resolve()

    if not str(resolved_path).startswith(str(DOWNLOAD_DIR_PATH)):
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not resolved_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(str(resolved_path),
                        media_type="video/mp4",
                        filename=filename,
                        headers={
                            "Cache-Control":
                            "no-cache, no-store, must-revalidate",
                            "Pragma": "no-cache",
                            "Expires": "0"
                        })


app.mount("/static", StaticFiles(directory="static"), name="static")
