"""Transcribe media with OpenAI Whisper, staying under its upload limit.

Audio is sent as mono 32 kbps MP3 (about 14 MB per hour). Anything still over
the limit is cut from the original into equal parts, each encoded on its own,
and segment timestamps are shifted by each part's start time.
"""
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
import json
import math
import re
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


def _plain(text):
    return re.sub(r"[^\w']", '', text.casefold())


def add_sentence_marks(words, segments):
    """Copy . ! ? from the segment text onto Whisper's unpunctuated words.

    Segments can end sentences mid-way ("...of code. AI agents..."), so the whole
    word list is aligned with the text's words (difflib). A local look-ahead lost
    its place at the first long mismatch and left the rest of a video unpunctuated.
    """
    tokens = [t for s in segments for t in (_field(s, 'text') or '').split()]
    matcher = SequenceMatcher(None, [_plain(w['word']) for w in words], [_plain(t) for t in tokens], autojunk=False)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            word, token = words[block.a + k], tokens[block.b + k]
            mark = token.rstrip('"\'”’)')[-1:]
            if mark in ('.', '!', '?') and not word['word'].endswith(('.', '!', '?')):
                word['word'] += mark


# Whisper stops punctuating long requests that ask for word times (a 28-minute
# video had no sentence ends after minute 11; the same audio in 4 minutes was
# fully punctuated), so word transcripts are requested in parts of this length.
WORD_PART_SECONDS = 600
PARALLEL_PARTS = 4


def pauses(source):
    """Midpoints, in seconds, of the source's silent stretches."""
    try:
        result = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(source), '-vn', '-af',
                                 # -30 dB / 0.25 s still finds the short gaps between sentences under background music
                                 'silencedetect=noise=-30dB:d=0.25', '-f', 'null', '-'], capture_output=True, text=True, timeout=900)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    starts = [float(t) for t in re.findall(r'silence_start: (-?[0-9.]+)', result.stderr)]
    ends = [float(t) for t in re.findall(r'silence_end: ([0-9.]+)', result.stderr)]
    return [(a + b) / 2 for a, b in zip(starts, ends)]


def cut_points(total, quiet, longest=WORD_PART_SECONDS):
    """Part starts plus the end: each cut at the latest pause in the minute before `longest`
    seconds of audio, so no word is split; at exactly `longest` when there is no pause."""
    points = [0.0]
    while total - points[-1] > longest:
        limit = points[-1] + longest
        near = [q for q in quiet if limit - 60 <= q <= limit]
        points.append(max(near) if near else limit)
    return points + [total]


def word_parts(source, folder):
    """[(mp3 path, start seconds)] of at most WORD_PART_SECONDS each (always under the upload limit)."""
    if not any(s.get('codec_type') == 'audio' for s in streams(source)):
        raise MediaError('This video has no audio track to transcribe.')
    total = duration(source)
    points = cut_points(total, pauses(source) if total > WORD_PART_SECONDS else [])
    parts = []
    for i, (start, end) in enumerate(zip(points, points[1:])):
        path = Path(folder) / f'words_{i:03d}.mp3'
        encode(source, path, start, end - start)
        parts.append((path, start))
    return parts


def transcribe_words(client, source):
    """[{word, start, end}] for the whole source, with sentence punctuation; times are in source seconds."""
    def part_words(part):
        path, offset = part
        with open(path, 'rb') as audio:
            result = client.audio.transcriptions.create(model='whisper-1', file=audio, response_format='verbose_json',
                                                        timestamp_granularities=['word', 'segment'])
        found = [dict(word=(_field(w, 'word') or '').strip(), start=round(_field(w, 'start') + offset, 3),
                      end=round(_field(w, 'end') + offset, 3)) for w in _field(result, 'words') or []]
        found = [w for w in found if w['word']]
        add_sentence_marks(found, _field(result, 'segments') or [])
        return found
    with tempfile.TemporaryDirectory(prefix='clipforge-openai-') as folder, ThreadPoolExecutor(PARALLEL_PARTS) as pool:
        return [w for found in pool.map(part_words, word_parts(source, folder)) for w in found]
