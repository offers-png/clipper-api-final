"""Transcribe media with OpenAI Whisper, staying under its upload limit.

Audio is sent as mono 32 kbps MP3 (about 14 MB per hour). Anything still over
the limit is cut from the original into equal parts, each encoded on its own,
and segment timestamps are shifted by each part's start time.
"""
import json
import math
from pathlib import Path
import subprocess
import tempfile

from url_media import MediaError, streams

OPENAI_MAX_BYTES = 24 * 1024 * 1024  # OpenAI rejects uploads over 25 MB.
BITRATE = '32k'
MAX_PARTS = 200


def encode(source, output, start=None, length=None):
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-y']
    if start is not None:
        command += ['-ss', f'{start:.3f}']
    command += ['-i', str(source)]
    if length is not None:
        command += ['-t', f'{length:.3f}']
    command += ['-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', BITRATE, str(output)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=1800)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raise MediaError('Audio conversion failed: FFmpeg is unavailable or timed out.') from None
    if result.returncode or not Path(output).is_file() or not Path(output).stat().st_size:
        raise MediaError('Audio conversion failed: the audio track could not be decoded.')


def duration(path):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(path)],
                                capture_output=True, text=True, timeout=60)
        return float(json.loads(result.stdout)['format']['duration'])
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        raise MediaError('Could not read the audio length.') from None


def prepare_parts(source, folder, max_bytes=OPENAI_MAX_BYTES):
    """[(mp3 path, start seconds)], each part no larger than max_bytes."""
    if not any(s.get('codec_type') == 'audio' for s in streams(source)):
        raise MediaError('This video has no audio track to transcribe.')
    whole = Path(folder) / 'audio.mp3'
    encode(source, whole)
    size = whole.stat().st_size
    if size <= max_bytes:
        return [(whole, 0.0)]
    total = duration(whole)
    whole.unlink()
    # Aim for 90% of the limit; add a part and retry if one still comes out too big.
    count = math.ceil(size / (max_bytes * 0.9))
    while count <= MAX_PARTS:
        length = total / count
        parts = []
        for i in range(count):
            part = Path(folder) / f'part_{count:03d}_{i:03d}.mp3'
            encode(source, part, i * length, length)
            parts.append((part, i * length))
        if all(path.stat().st_size <= max_bytes for path, _ in parts):
            return parts
        count += 1
    raise MediaError('The audio could not be split into small enough parts.')


def _field(item, name):
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def transcribe(client, source, max_bytes=OPENAI_MAX_BYTES):
    """(text, segments) for the whole source; segment times are in source seconds."""
    texts, segments = [], []
    with tempfile.TemporaryDirectory(prefix='clipforge-openai-') as folder:
        for path, offset in prepare_parts(source, folder, max_bytes):
            with open(path, 'rb') as audio:
                result = client.audio.transcriptions.create(model='whisper-1', file=audio, response_format='verbose_json')
            text = result if isinstance(result, str) else _field(result, 'text') or ''
            texts.append(text.strip())
            for segment in ([] if isinstance(result, str) else _field(result, 'segments') or []):
                segments.append(dict(start=round(_field(segment, 'start') + offset, 3),
                                     end=round(_field(segment, 'end') + offset, 3),
                                     text=(_field(segment, 'text') or '').strip()))
    return ' '.join(t for t in texts if t), segments
