"""Download public media and validate it before invoking transcription.

Never loads browser cookies or emits downloader diagnostics containing private URLs.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

import requests

PLATFORMS = ('youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com',
             'facebook.com', 'x.com', 'twitter.com', 'soundcloud.com', 'vimeo.com')


class MediaError(RuntimeError):
    pass


def streams(path):
    if not Path(path).is_file() or not Path(path).stat().st_size:
        raise MediaError('Downloaded media is empty or missing.')
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams',
                                 '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise MediaError('The URL did not return readable media. It may require sign-in or be blocked.')
        return json.loads(result.stdout).get('streams', [])
    except FileNotFoundError:
        raise MediaError('FFprobe is not installed on this server.') from None
    except (ValueError, subprocess.TimeoutExpired):
        raise MediaError('Media inspection failed or timed out.') from None


def download(url):
    parsed = urlparse(url.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise MediaError('Provide a public HTTP or HTTPS media URL.')
    root = Path(tempfile.mkdtemp(prefix='clipforge-url-'))
    try:
        host = parsed.hostname.lower()
        if any(host == domain or host.endswith('.' + domain) for domain in PLATFORMS):
            result = subprocess.run([
                sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
                '--no-progress', '--no-simulate', '-f', 'bv*+ba/b',
                '--merge-output-format', 'mp4', '-o', str(root / 'source.%(ext)s'),
                '--print', 'after_move:filepath', '--', url.strip(),
            ], capture_output=True, text=True, timeout=900)
            if result.returncode:
                raise MediaError('Video download failed. The service may block server downloads or require sign-in; upload the video file instead.')
            lines = result.stdout.strip().splitlines()
            path = Path(lines[-1]).resolve() if lines else root / 'missing'
            if path.parent != root.resolve():
                raise MediaError('Downloader did not return the expected media file.')
        else:
            path = root / 'source.media'
            with requests.get(url.strip(), stream=True, timeout=(15, 60)) as response:
                if response.status_code != 200:
                    raise MediaError(f'Media download returned HTTP {response.status_code}. Upload the video file instead.')
                content_type = response.headers.get('Content-Type', '').lower()
                if any(kind in content_type for kind in ('text/', 'json', 'xml')):
                    raise MediaError('The URL returned a webpage instead of media. Upload the video file instead.')
                with path.open('wb') as output:
                    for chunk in response.iter_content(1024 * 1024):
                        output.write(chunk)
        if not any(s.get('codec_type') in ('audio', 'video') for s in streams(path)):
            raise MediaError('Downloaded file contains no audio or video streams.')
        # Return a standalone file: existing callers own and delete this path.
        descriptor, final = tempfile.mkstemp(prefix='clipforge-media-', suffix=path.suffix)
        os.close(descriptor)
        try:
            shutil.move(str(path), final)
        except Exception:
            Path(final).unlink(missing_ok=True)
            raise
        return final
    except MediaError:
        raise
    except (requests.RequestException, subprocess.SubprocessError, OSError):
        raise MediaError('Media download failed or timed out. Upload the video file instead.') from None
    finally:
        shutil.rmtree(root)


def audio_to_mp3(source):
    if not any(s.get('codec_type') == 'audio' for s in streams(source)):
        raise MediaError('This video has no audio track to transcribe.')
    descriptor, output = tempfile.mkstemp(prefix='clipforge-audio-', suffix='.mp3')
    os.close(descriptor)
    try:
        result = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error',
                                 '-y', '-i', str(source), '-map', '0:a:0', '-vn',
                                 '-c:a', 'libmp3lame', '-b:a', '192k', output],
                                capture_output=True, timeout=120)
        if result.returncode or not Path(output).stat().st_size:
            raise MediaError('Audio conversion failed: the audio track could not be decoded.')
        return output
    except Exception as error:
        Path(output).unlink(missing_ok=True)
        if isinstance(error, MediaError):
            raise
        raise MediaError('Audio conversion failed: FFmpeg is unavailable or timed out.') from None
