# utils.py — ffmpeg helpers, paths, download, durations

import os, tempfile, subprocess, asyncio, requests
from typing import Optional, Tuple

BASE_DIR   = "/data"
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PREVIEW_DIR= os.path.join(BASE_DIR, "previews")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
TMP_DIR    = "/tmp"

def ensure_dirs():
    for d in (UPLOAD_DIR, PREVIEW_DIR, EXPORT_DIR, TMP_DIR):
        os.makedirs(d, exist_ok=True)

def PUBLIC_BASE_FROM(request) -> str:
    if request is None: return ""
    return str(request.base_url).rstrip("/")

def abs_url(request, path: Optional[str]) -> Optional[str]:
    if not path: return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base_env = os.getenv("PUBLIC_BASE", "").rstrip("/")
    base = base_env or PUBLIC_BASE_FROM(request)
    return f"{base}{path}"

def safe(name: str) -> str:
    return "".join(c for c in (name or "file") if c.isalnum() or c in ("-", "_", "."))[:120]

def seconds_between(start: str, end: str) -> int:
    def hhmmss_to_seconds(s: str) -> float:
        s = s.strip()
        parts = [float(p) for p in s.split(":")]
        if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
        if len(parts) == 2: return parts[0]*60 + parts[1]
        return float(s)
    val = max(0.0, hhmmss_to_seconds(end) - hhmmss_to_seconds(start))
    return int(val)

def add_watermark_drawtext(text: str) -> str:
    t = (text or "").replace("'", r"\'")
    return (
        f"drawtext=text='{t}':x=w-tw-20:y=h-th-20:"
        "fontcolor=white:fontsize=28:box=1:boxcolor=black@0.45:boxborderw=10"
    )

async def _run(cmd, timeout=1200) -> Tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return (1, "Timed out")
    out = (stdout or b"").decode() + "\n" + (stderr or b"").decode()
    return proc.returncode, out.strip()

def ffprobe_duration(path: Optional[str]) -> Optional[float]:
    if not path or not os.path.exists(path): return None
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path
        ])
        s = out.decode().strip().splitlines()[-1]
        return float(s)
    except Exception:
        return None

def file_size(path: Optional[str]) -> Optional[int]:
    try:
        return os.path.getsize(path) if path and os.path.exists(path) else None
    except Exception:
        return None

async def run_ffmpeg_preview(src_path: str, start: str, end: str, out_path: str, drawtext: Optional=str):
    # attempt stream copy (fast) if no drawtext; else encode 480p
    if not drawtext:
        cmd = ["ffmpeg","-hide_banner","-loglevel","error","-ss",start,"-to",end,
               "-i",src_path,"-c","copy","-movflags","+faststart","-y",out_path]
        code, err = await _run(cmd, timeout=600)
        if code==0 and os.path.exists(out_path):
            return True, ""
        # fallback encode
    vf = ["-vf", drawtext] if drawtext else []
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-ss",start,"-to",end,"-i",src_path,
           "-c:v","libx264","-preset","veryfast","-crf","26",
           "-c:a","aac","-b:a","128k","-movflags","+faststart",*vf,"-y",out_path]
    return await _run(cmd, timeout=900)

async def run_ffmpeg_final(src_path: str, start: str, end: str, out_path: str, drawtext: Optional=str):
    vf = ["-vf", f"scale=-2:1080:flags=lanczos,{drawtext}"] if drawtext else ["-vf","scale=-2:1080:flags=lanczos"]
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-ss",start,"-to",end,"-i",src_path,
           "-c:v","libx264","-preset","faster","-crf","20",
           "-c:a","aac","-b:a","192k","-movflags","+faststart",*vf,"-y",out_path]
    return await _run(cmd, timeout=1800)

async def to_mp3_for_whisper(in_path: str) -> str:
    mp3_path = os.path.join(TMP_DIR, f"aud_{os.path.basename(in_path)}.mp3")
    code = subprocess.call(["ffmpeg","-y","-i",in_path,"-vn","-acodec","libmp3lame","-b:a","192k",mp3_path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if code != 0 or not os.path.exists(mp3_path):
        raise RuntimeError("ffmpeg mp3 conversion failed")
    return mp3_path

async def download_to_tmp(url: str) -> str:
    """yt-dlp for platforms; fallback HTTP. Returns a local video path."""
    tmp_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    u = (url or "").lower()
    if any(k in u for k in ["youtube","youtu.be","tiktok.com","instagram.com","facebook.com","x.com","twitter.com","soundcloud.com","vimeo.com"]):
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp","-f","mp4","-o",tmp_path,"--no-playlist","--force-overwrites",url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(tmp_path):
            raise RuntimeError(f"yt-dlp failed: {stderr.decode()[:500]}")
    else:
        r = requests.get(url, stream=True, timeout=60)
        if r.status_code != 200: raise RuntimeError(f"HTTP {r.status_code} while fetching URL")
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(1024*1024):
                f.write(chunk)
    return tmp_path
