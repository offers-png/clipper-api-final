# Shared FFmpeg helpers copied from the existing app; no cloud/auth imports.
import os, subprocess
from datetime import datetime
from typing import Optional, List, Tuple
from pathlib import Path
from .resource_paths import DATA
BASE_DIR = str(DATA)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PREVIEW_DIR = os.path.join(BASE_DIR, "previews")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
THUMB_DIR = os.path.join(BASE_DIR, "thumbs")
TMP_DIR = os.path.join(BASE_DIR, "tmp")
for folder in (UPLOAD_DIR, PREVIEW_DIR, EXPORT_DIR, THUMB_DIR, TMP_DIR):
    os.makedirs(folder, exist_ok=True)

def nowstamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")

def safe(name: str) -> str:
    return "".join(c for c in (name or "file") if c.isalnum() or c in ("-", "_", "."))[:120]

def run(cmd: List[str], timeout=1200) -> Tuple[int, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return p.returncode, (p.stdout + "\n" + p.stderr).strip()

def scale_filter(h: int) -> str:
    return f"scale=-2:{h}:flags=lanczos"

def compose_vf(scale: Optional[str], drawtext: Optional[str]) -> List[str]:
    if scale and drawtext: return ["-vf", f"{scale},drawtext={drawtext}"]
    if scale:              return ["-vf", scale]
    if drawtext:           return ["-vf", f"drawtext={drawtext}"]
    return []

def drawtext_expr(text: str) -> str:
    t = (text or "").replace("'", r"\'")
    return (
        f"text='{t}':x=w-tw-20:y=h-th-20:"
        "fontcolor=white:fontsize=28:box=1:boxcolor=black@0.45:boxborderw=10"
    )

def hhmmss_to_seconds(s: str) -> float:
    s = s.strip()
    parts = [float(p) for p in s.split(":")]
    if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
    if len(parts) == 2: return parts[0]*60 + parts[1]
    return float(s)

def duration_from(start: str, end: str) -> float:
    return max(0.1, hhmmss_to_seconds(end) - hhmmss_to_seconds(start))

def seconds_to_text(x: float) -> str:
    x = max(0, int(round(x)))
    h = x // 3600
    m = (x % 3600) // 60
    s = x % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def ffprobe_duration(path: str) -> Optional[float]:
    try:
        code, out = run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path
        ], timeout=30)
        if code == 0 and out.strip():
            return float(out.strip().splitlines()[-1])
    except Exception:
        pass
    return None

def make_thumbnail(source_path: str, t_start: str, out_path: str):
    # Grab a frame ~0.25s after start to avoid black frames on cuts
    seek = max(0.0, hhmmss_to_seconds(t_start) + 0.25)
    code, err = run([
        "ffmpeg","-hide_banner","-loglevel","error",
        "-ss", str(seek), "-i", source_path,
        "-frames:v","1","-vf","scale=480:-1",
        "-y", out_path
    ], timeout=30)
    if code != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"thumbnail failed: {err[:300]}")

async def build_clip(
    source_path: str,
    start: str,
    end: str,
    want_preview: bool,
    want_final: bool,
    watermark_text: Optional[str],
) -> dict:
    base = safe(os.path.splitext(os.path.basename(source_path))[0])
    stamp = nowstamp()
    dur_s = duration_from(start, end)

    prev_name  = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_prev_{stamp}.mp4"
    final_name = f"{base}_{start.replace(':','-')}-{end.replace(':','-')}_1080_{stamp}.mp4"
    prev_out   = os.path.join(PREVIEW_DIR, prev_name)
    final_out  = os.path.join(EXPORT_DIR,  final_name)

    # preview
    if want_preview and not watermark_text:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", str(dur_s), "-i", source_path,
            "-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-movflags","+faststart","-y", prev_out
        ], timeout=300)
        if code != 0 or not os.path.exists(prev_out):
            code, err = run([
                "ffmpeg","-hide_banner","-loglevel","error",
                "-ss", start, "-t", str(dur_s), "-i", source_path,
                "-c:v","libx264","-preset","veryfast","-crf","28",
                "-c:a","aac","-b:a","128k",
                "-movflags","+faststart","-y", prev_out
            ], timeout=600)
            if code != 0 or not os.path.exists(prev_out):
                raise RuntimeError(f"Preview failed: {err[:500]}")
    elif want_preview and watermark_text:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", str(dur_s), "-i", source_path,
            "-c:v","libx264","-preset","veryfast","-crf","26",
            "-c:a","aac","-b:a","128k",
            *compose_vf(scale_filter(480), drawtext_expr(watermark_text)),
            "-movflags","+faststart","-y", prev_out
        ], timeout=900)
        if code != 0 or not os.path.exists(prev_out):
            raise RuntimeError(f"Preview watermark failed: {err[:500]}")

    # final
    if want_final:
        code, err = run([
            "ffmpeg","-hide_banner","-loglevel","error",
            "-ss", start, "-t", str(dur_s), "-i", source_path,
            "-c:v","libx264","-preset","faster","-crf","20",
            "-c:a","aac","-b:a","192k",
            *compose_vf(scale_filter(1080), drawtext_expr(watermark_text) if watermark_text else None),
            "-movflags","+faststart","-y", final_out
        ], timeout=1800)
        if code != 0 or not os.path.exists(final_out):
            raise RuntimeError(f"Final export failed: {err[:500]}")

    # thumbnail
    thumb_name = f"{base}_{start.replace(':','-')}_{stamp}.jpg"
    thumb_out  = os.path.join(THUMB_DIR, thumb_name)
    try:
        make_thumbnail(source_path, start, thumb_out)
    except Exception as e:
        # fall back to generating from preview if source seek fails
        if os.path.exists(prev_out):
            try: make_thumbnail(prev_out, "00:00:00", thumb_out)
            except Exception as _:
                thumb_out = None
        else:
            thumb_out = None

    result = {
        "preview_path": prev_out if os.path.exists(prev_out) else None,
        "final_path":   final_out if os.path.exists(final_out) else None,
        "thumb_path":   thumb_out if thumb_out and os.path.exists(thumb_out) else None,
        "duration_seconds": dur_s,
        "start": start,
        "end": end
    }
    return result
