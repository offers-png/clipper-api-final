"""/transcribe sources: supported platforms or uploads only, public addresses, size and length caps."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
from fastapi.testclient import TestClient
import app as backend
import url_media
from url_media import MediaTooLarge, UnsupportedSource, check_media_limits, check_transcribe_url
from rate_limit import RateLimiter
from tests.test_endpoint_auth import signed_in, as_user

def resolves_to(*addresses):
    return lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, proto, '', (a, port)) for a in addresses]

def make_video(path, seconds):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=d={seconds}:s=160x120',
                    '-f', 'lavfi', '-i', f'sine=d={seconds}', '-shortest', str(path)], check=True, timeout=60)


class TranscribeUrlTests(unittest.TestCase):
    def test_supported_platforms_on_public_addresses_are_allowed(self):
        for url in ['https://www.youtube.com/watch?v=abc', 'https://youtu.be/abc', 'https://m.youtube.com/shorts/abc',
                    'https://www.tiktok.com/@a/video/1', 'https://vm.tiktok.com/ZM1/', 'https://www.instagram.com/reel/abc/']:
            with self.subTest(url=url):
                check_transcribe_url(url, resolves_to('142.250.80.46'))

    def test_other_hosts_and_schemes_are_rejected_with_a_clear_message(self):
        for url in ['https://www.facebook.com/watch?v=1', 'https://vimeo.com/1', 'https://example.com/video.mp4',
                    'http://127.0.0.1:8000/media/a.mp4', 'http://localhost/a.mp4', 'http://169.254.169.254/latest/',
                    'https://youtube.com.evil.example/a', 'https://evilyoutube.com/a', 'ftp://youtube.com/a',
                    'https://user:pass@youtube.com/a', 'https://youtube.com:99999/a', 'file:///etc/passwd', '']:
            with self.subTest(url=url):
                with self.assertRaisesRegex(UnsupportedSource, 'Only YouTube, TikTok and Instagram'):
                    check_transcribe_url(url, resolves_to('142.250.80.46'))

    def test_platform_names_resolving_to_private_addresses_are_rejected(self):
        for address in ['10.0.0.5', '127.0.0.1', '192.168.1.1', '172.16.0.1', '169.254.169.254',
                        '100.64.0.1', '0.0.0.0', '::1', 'fd00::1', '::ffff:127.0.0.1', '::ffff:10.0.0.1']:
            with self.subTest(address=address):
                with self.assertRaisesRegex(UnsupportedSource, 'private or internal'):
                    check_transcribe_url('https://www.youtube.com/watch?v=abc', resolves_to('142.250.80.46', address))

    def test_unresolvable_host_is_rejected(self):
        def fail(*args, **kwargs):
            raise socket.gaierror('not found')
        with self.assertRaisesRegex(UnsupportedSource, 'could not be found'):
            check_transcribe_url('https://www.youtube.com/watch?v=abc', fail)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class MediaLimitTests(unittest.TestCase):
    def test_real_video_size_and_length_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / 'three-seconds.mp4'
            make_video(video, 3)
            check_media_limits(video, 500 * 1024 * 1024, 30 * 60)
            with self.assertRaisesRegex(MediaTooLarge, 'longer than 0.0333333 minutes'):
                check_media_limits(video, 500 * 1024 * 1024, 2)
            with self.assertRaisesRegex(MediaTooLarge, 'Videos over 0 MB'):
                check_media_limits(video, 100, 30 * 60)

    def test_platform_download_passes_limits_and_explains_skipped_videos(self):
        for output, message in [('[download] abc: does not pass filter (duration <=? 1800), skipping', 'longer than 30 minutes'),
                                ('[download] File is larger than max-filesize (600MB > 500MB). Aborting.', 'over 500 MB')]:
            with self.subTest(message=message):
                run = Mock(return_value=Mock(returncode=0, stdout='', stderr=output))
                with tempfile.TemporaryDirectory() as folder, patch('url_media.subprocess.run', run):
                    with self.assertRaisesRegex(MediaTooLarge, message):
                        url_media.platform_download('https://youtu.be/abc', Path(folder), 500 * 1024 * 1024, 1800)
                command = run.call_args.args[0]
                self.assertIn('--max-filesize', command)
                self.assertEqual(command[command.index('--match-filter') + 1], 'duration <=? 1800')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class TranscribeRouteTests(unittest.TestCase):
    def setUp(self):
        self.openai = MagicMock()
        self.openai.audio.transcriptions.create.return_value = 'route transcript'
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        for patcher in [patch.object(backend, 'billing_actor', signed_in), patch.object(backend, 'OPENAI_API_KEY', 'test-key'),
                        patch.object(backend, 'client', self.openai), patch.object(backend, 'ai_limiter', RateLimiter(1000, 600)),
                        patch.object(backend, 'insert_transcript', return_value='r1'),
                        patch('url_media.socket.getaddrinfo', resolves_to('142.250.80.46'))]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(backend.app)

    def post(self, **kwargs):
        return self.client.post('/transcribe', headers=as_user('alice'), **kwargs)

    def video(self, name, seconds):
        path = Path(self.folder) / name
        make_video(path, seconds)
        return path

    def test_unsupported_links_are_rejected_before_downloading(self):
        with patch.object(backend, 'download_to_tmp') as download:
            for url in ['https://www.facebook.com/watch?v=1', 'http://127.0.0.1:8000/admin',
                        'http://evil.example/media/previews/..%2F..%2Fapp.py']:
                with self.subTest(url=url):
                    response = self.post(data={'url': url})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('Only YouTube, TikTok and Instagram links are supported', response.json()['error'])
        download.assert_not_called(); self.openai.audio.transcriptions.create.assert_not_called()

    def test_platform_link_resolving_to_internal_address_is_rejected(self):
        with patch('url_media.socket.getaddrinfo', resolves_to('10.1.2.3')), patch.object(backend, 'download_to_tmp') as download:
            response = self.post(data={'url': 'https://www.youtube.com/watch?v=abc'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('private or internal', response.json()['error'])
        download.assert_not_called()

    def test_supported_link_downloads_with_limits_and_transcribes(self):
        downloaded = self.video('download.mp4', 2)
        with patch.object(backend, 'download_to_tmp', return_value=str(downloaded)) as download:
            response = self.post(data={'url': 'https://www.tiktok.com/@a/video/1'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['text'], 'route transcript')
        download.assert_called_once_with('https://www.tiktok.com/@a/video/1', backend.TRANSCRIBE_MAX_BYTES, backend.TRANSCRIBE_MAX_SECONDS)
        self.assertEqual((backend.TRANSCRIBE_MAX_BYTES, backend.TRANSCRIBE_MAX_SECONDS), (500 * 1024 * 1024, 1800))

    def test_oversized_upload_is_rejected_with_413(self):
        with patch.object(backend, 'TRANSCRIBE_MAX_BYTES', 1024 * 1024):
            response = self.post(files={'file': ('big.mp4', b'0' * (1024 * 1024 + 1))})
        self.assertEqual(response.status_code, 413)
        self.assertIn('Videos over 1 MB are not supported', response.json()['error'])
        self.openai.audio.transcriptions.create.assert_not_called()

    def test_overlong_upload_is_rejected_with_413(self):
        video = self.video('long.mp4', 3)
        with patch.object(backend, 'TRANSCRIBE_MAX_SECONDS', 2):
            response = self.post(files={'file': ('long.mp4', video.read_bytes())})
        self.assertEqual(response.status_code, 413)
        self.assertIn('Videos longer than', response.json()['error'])
        self.openai.audio.transcriptions.create.assert_not_called()

    def test_upload_within_limits_is_transcribed(self):
        video = self.video('ok.mp4', 2)
        response = self.post(files={'file': ('ok.mp4', video.read_bytes())})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['text'], 'route transcript')

    def test_this_servers_own_preview_link_is_transcribed_and_kept(self):
        name = 'test_preview_%d.mp4' % os.getpid()
        preview = Path(backend.PREVIEW_DIR) / name
        make_video(preview, 2)
        self.addCleanup(preview.unlink, True)
        with patch.object(backend, 'download_to_tmp') as download:
            response = self.post(data={'url': 'https://clipper-api-final-1.onrender.com/media/previews/' + name})
        self.assertEqual(response.status_code, 200, response.text)
        download.assert_not_called()
        self.assertTrue(preview.is_file(), 'the shared preview file must not be deleted')


if __name__ == '__main__':
    unittest.main()
