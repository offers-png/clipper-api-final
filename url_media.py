"""Download public media and validate it before invoking transcription.

Never emits downloader diagnostics containing private URLs. Browser cookies are used only to retry
a YouTube link that asks for sign-in or bot confirmation (CLIPFORGE_YTDLP_COOKIES_BROWSER, default
chrome; empty turns it off).
"""
import ipaddress
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

import requests

PLATFORMS = ('youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com',
             'facebook.com', 'x.com', 'twitter.com', 'soundcloud.com', 'vimeo.com')


# /transcribe accepts uploads plus links from these hosts only.
TRANSCRIBE_PLATFORMS = ('youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com')
UNSUPPORTED_LINK = 'Only YouTube, TikTok and Instagram links are supported. For anything else, upload the video file.'


class MediaError(RuntimeError):
    pass


class UnsupportedSource(MediaError):
    pass


class MediaTooLarge(MediaError):
    pass


def size_message(max_bytes):
    return f'Videos over {max_bytes // (1024 * 1024)} MB are not supported. Trim or compress the video and try again.'


def length_message(max_seconds):
    return f'Videos longer than {max_seconds / 60:g} minutes are not supported. Trim the video and try again.'


def check_transcribe_url(url, resolve=None):
    """Allow only the supported platforms, and only when they resolve to public addresses."""
    parsed = urlparse((url or '').strip())
    host = (parsed.hostname or '').lower().rstrip('.')
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        raise UnsupportedSource(UNSUPPORTED_LINK) from None
    if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or not any(host == domain or host.endswith('.' + domain) for domain in TRANSCRIBE_PLATFORMS)):
        raise UnsupportedSource(UNSUPPORTED_LINK)
    try:
        addresses = {info[4][0].split('%')[0] for info in (resolve or socket.getaddrinfo)(host, port, proto=socket.IPPROTO_TCP)}
    except (OSError, UnicodeError):
        raise UnsupportedSource("That link's host could not be found. Check the link or upload the video file.") from None
    for address in addresses:
        ip = ipaddress.ip_address(address)
        # Python 3.11 reports IPv4-mapped IPv6 loopback/private addresses as global.
        ip = getattr(ip, 'ipv4_mapped', None) or ip
        if not ip.is_global:
            raise UnsupportedSource('Links that point to private or internal network addresses are not allowed.')


def check_media_limits(path, max_bytes, max_seconds):
    if Path(path).stat().st_size > max_bytes:
        raise MediaTooLarge(size_message(max_bytes))
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                 '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
        duration = float(json.loads(result.stdout)['format']['duration'])
    except FileNotFoundError:
        raise MediaError('FFprobe is not installed on this server.') from None
    except (ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise MediaError('Could not read the video length. Upload a standard video file.') from None
    if duration > max_seconds:
        raise MediaTooLarge(length_message(max_seconds))


YOUTUBE_BLOCKED = 'YouTube blocked this link. Download the video and upload the file instead.'


def update_downloader():
    """Install the newest yt-dlp: YouTube changes often and older versions stop working. Runs at backend
    start (CLIPFORGE_YTDLP_AUTO_UPDATE=0 turns it off); yt-dlp runs as its own process, so later downloads
    use the new version at once. A failed update keeps the installed one."""
    if os.getenv('CLIPFORGE_YTDLP_AUTO_UPDATE', '1') == '0':
        return False
    try:
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', '--quiet',
                                 '--disable-pip-version-check', 'yt-dlp'], capture_output=True, text=True, timeout=300)
    except (subprocess.SubprocessError, OSError) as error:
        logging.getLogger(__name__).warning('yt-dlp update failed: %s', type(error).__name__)
        return False
    from importlib.metadata import version
    logging.getLogger(__name__).warning('yt-dlp update %s; now %s', 'failed' if result.returncode else 'done', version('yt-dlp'))
    return not result.returncode


def youtube_url(url):
    host = (urlparse(url).hostname or '').lower()
    return any(host == domain or host.endswith('.' + domain) for domain in ('youtube.com', 'youtu.be'))


def needs_sign_in(error):
    """YouTube's 'Sign in to confirm you're not a bot' (and other sign-in/login demands)."""
    return any(text in error for text in ('sign in', 'not a bot', 'login', 'cookies'))


def platform_url(url):
    host=(urlparse(url).hostname or '').lower()
    return any(host == domain or host.endswith('.' + domain) for domain in PLATFORMS)


# At most 1080p: the best format is often 4K, which takes long to fetch and
# can pass the size limit mid-download (leaving nothing usable). H.264 first:
# YouTube's default AV1 decodes ~10x slower on CPU (scene detection, renders)
# and does not play in every browser.
VIDEO_FORMAT = 'bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b'
AUDIO_FORMAT = 'ba/b'


def platform_download(url, root, max_bytes=None, max_seconds=None, audio_only=False):
    # Optional limits skip oversized videos before downloading when the host reports
    # size/length; callers still verify the downloaded file.
    limits = []
    if max_bytes:
        limits += ['--max-filesize', str(max_bytes)]
    if max_seconds:
        limits += ['--match-filter', f'duration <=? {int(max_seconds)}']
    def run(*extra):
        return subprocess.run([
            sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
            '--no-progress', '--no-simulate', '--socket-timeout', '30',
            '--retries', '2', '--fragment-retries', '2', *extra,
            '-f', AUDIO_FORMAT if audio_only else VIDEO_FORMAT, '--merge-output-format', 'mp4', *limits,
            '-o', str(root / 'source.%(ext)s'), '--print', 'after_move:filepath',
            '--', url.strip(),
        ], capture_output=True, text=True, timeout=900)
    result = run()
    browser = os.getenv('CLIPFORGE_YTDLP_COOKIES_BROWSER', 'chrome').strip()
    if result.returncode and youtube_url(url) and browser and needs_sign_in((result.stderr or '').lower()):
        logging.getLogger(__name__).info('YouTube asked for sign-in; retrying with %s cookies', browser)
        result = run('--cookies-from-browser', browser)
    skipped = ((result.stdout or '') + (result.stderr or '')).lower()
    if not result.returncode and not result.stdout.strip():
        if max_seconds and 'does not pass filter' in skipped:
            raise MediaTooLarge(length_message(max_seconds))
        if max_bytes and 'max-filesize' in skipped:
            raise MediaTooLarge(size_message(max_bytes))
    if result.returncode:
        # Inspect diagnostic categories without exposing arbitrary stderr or URLs.
        error=(result.stderr or '').lower()
        if youtube_url(url) and 'timed out' not in error and 'timeout' not in error:
            raise MediaError(YOUTUBE_BLOCKED)
        if 'ip address is blocked' in error or 'http error 403' in error:
            raise MediaError('The video host blocked this server or network (HTTP 403/IP restriction). Upload the video file instead.')
        if 'login' in error or 'sign in' in error:
            raise MediaError('This video requires sign-in at its host. Upload a video file you can access.')
        if 'timed out' in error or 'timeout' in error:
            raise MediaError('The video host timed out. Retry later or upload the video file.')
        raise MediaError('Video download failed. The service may block server downloads or require sign-in; upload the video file instead.')
    lines = result.stdout.strip().splitlines()
    path = Path(lines[-1]).resolve() if lines else root / 'missing'
    if path.parent != root.resolve():
        raise MediaError('Downloader did not return the expected media file.')
    return path


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


def download(url, max_bytes=None, max_seconds=None, audio_only=False):
    """audio_only fetches just the sound track from video sites (enough for transcription)."""
    started=time.monotonic()
    parsed = urlparse(url.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise MediaError('Provide a public HTTP or HTTPS media URL.')
    root = Path(tempfile.mkdtemp(prefix='clipforge-url-'))
    try:
        if platform_url(url):
            path = platform_download(url, root, max_bytes, max_seconds, audio_only)
        else:
            path = root / 'source.media'
            redirected_platform = None
            with requests.get(url.strip(), stream=True, timeout=(15, 60)) as response:
                if response.status_code != 200:
                    raise MediaError(f'Media download returned HTTP {response.status_code}. Upload the video file instead.')
                content_type = response.headers.get('Content-Type', '').lower()
                if 'html' in content_type and platform_url(response.url):
                    redirected_platform = response.url
                else:
                    # Some working MP4/CDN links use text/plain or octet-stream.
                    # Validate actual bytes with FFprobe instead of trusting MIME.
                    with path.open('wb') as output:
                        for chunk in response.iter_content(1024 * 1024):
                            output.write(chunk)
            if redirected_platform:
                path = platform_download(redirected_platform, root, audio_only=audio_only)
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
    except MediaError as error:
        logging.getLogger(__name__).warning('URL download failed kind=%s elapsed=%.2fs reason=%s',
            'platform' if platform_url(url) else 'direct', time.monotonic()-started, str(error))
        raise
    except (requests.RequestException, subprocess.SubprocessError, OSError) as error:
        logging.getLogger(__name__).warning('URL download failed kind=%s elapsed=%.2fs exception=%s',
            'platform' if platform_url(url) else 'direct', time.monotonic()-started, type(error).__name__)
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
