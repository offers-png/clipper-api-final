"""Actual FFmpeg/HTTP media tests; no cloud transcription or generation calls."""
from functools import partial
import ast
import asyncio
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from fastapi import UploadFile, File, Form
from fastapi.responses import JSONResponse
from io import BytesIO
import json

from clipforge_studios.flags import studios_enabled
from url_media import MediaError, audio_to_mp3, download, streams


class QuietHandler(SimpleHTTPRequestHandler):
    def guess_type(self, path):
        return 'text/plain' if path.endswith('wrong-type.mp4') else super().guess_type(path)

    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302); self.send_header('Location','/video.mp4'); self.end_headers(); return
        super().do_GET()

    def log_message(self, *args):
        pass


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'color=c=blue:s=160x90:d=1', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=1', '-c:v', 'libx264',
                        '-c:a', 'aac', '-shortest', str(cls.root/'video.mp4')], check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(cls.root/'video.mp4'),
                        '-an', '-c:v', 'copy', str(cls.root/'silent.mp4')], check=True)
        (cls.root/'blocked.html').write_text('<html>Sign in</html>')
        (cls.root/'empty.mp4').touch()
        shutil.copy(cls.root/'video.mp4', cls.root/'wrong-type.mp4')
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=cls.temp.name))
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}/'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join(); cls.temp.cleanup()

    def test_real_http_download_and_audio_conversion(self):
        source = download(self.base+'video.mp4')
        output = None
        try:
            output = audio_to_mp3(source)
            self.assertNotEqual(source, output)
            self.assertTrue(any(s['codec_type']=='audio' for s in streams(output)))
        finally:
            Path(source).unlink(missing_ok=True)
            if output: Path(output).unlink(missing_ok=True)

    def test_real_mp4_with_incorrect_text_content_type(self):
        source=download(self.base+'wrong-type.mp4')
        try:self.assertTrue(any(s['codec_type']=='audio' for s in streams(source)))
        finally:Path(source).unlink()

    def test_http_redirect_to_real_mp4(self):
        source=download(self.base+'redirect')
        try:self.assertTrue(any(s['codec_type']=='video' for s in streams(source)))
        finally:Path(source).unlink()

    def test_mp3_input_is_not_overwritten(self):
        source = audio_to_mp3(self.root/'video.mp4')
        try:
            before = Path(source).read_bytes()
            output = audio_to_mp3(source)
            try:
                self.assertNotEqual(source, output)
                self.assertEqual(before, Path(source).read_bytes())
            finally: Path(output).unlink()
        finally: Path(source).unlink()

    def test_no_audio_has_actionable_error(self):
        with self.assertRaisesRegex(MediaError, 'no audio track'):
            audio_to_mp3(self.root/'silent.mp4')

    def test_html_and_empty_media_rejected(self):
        for name in ('blocked.html', 'empty.mp4'):
            with self.subTest(name=name), self.assertRaises(MediaError):
                download(self.base+name)

    def test_downloader_failure_does_not_echo_sensitive_output(self):
        with patch('url_media.subprocess.run', return_value=subprocess.CompletedProcess([],1,'','private-token')):
            with self.assertRaises(MediaError) as caught:
                download('https://www.tiktok.com/@example/video/123')
        self.assertNotIn('private-token', str(caught.exception))

    def test_actual_downloader_output_path_and_audio_format(self):
        real_run = subprocess.run
        def fake_downloader(command, **kwargs):
            if command[1:3] == ['-m', 'yt_dlp']:
                self.assertIn('bv*+ba/b', command)
                self.assertIn('--ignore-config', command)
                template = command[command.index('-o')+1]
                destination = template.replace('%(ext)s', 'mkv')
                shutil.copy(self.root/'video.mp4', destination)
                return subprocess.CompletedProcess(command, 0, destination+'\n', '')
            return real_run(command, **kwargs)
        with patch('url_media.subprocess.run', side_effect=fake_downloader):
            source = download('https://vm.tiktok.com/example/')
        try:
            self.assertTrue(source.endswith('.mkv'))
            self.assertTrue(any(s['codec_type']=='audio' for s in streams(source)))
        finally: Path(source).unlink()

    def test_original_transcribe_route_converts_and_cleans_files(self):
        # Execute the real route body without bootstrapping cloud integrations.
        self.check_transcribe_route('video.mp4', 200, True)

    def test_original_transcribe_route_rejects_silent_video_before_api(self):
        self.check_transcribe_route('silent.mp4', 422, False)

    def check_transcribe_route(self, filename, status, calls_api):
        import app as backend
        source = Path(__file__).resolve().parents[1]/'app.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name=='transcribe_audio')
        function.decorator_list=[]
        client=Mock()
        client.audio.transcriptions.create.return_value='Offline API contract response'
        database=Mock(return_value=None)
        with tempfile.TemporaryDirectory() as uploads:
            scope=dict(UploadFile=UploadFile, File=File, Form=Form, JSONResponse=JSONResponse,
                       os=os, asyncio=asyncio, safe=lambda value:value, nowstamp=lambda:'test', UPLOAD_DIR=uploads,
                       audio_to_mp3=audio_to_mp3, MediaError=MediaError, client=client,
                       insert_transcript=database, print=Mock(), Request=object,
                       ai_actor=Mock(return_value={'email':'offline-contract-user'}),
                       save_upload=backend.save_upload, own_media_file=backend.own_media_file,
                       check_transcribe_url=backend.check_transcribe_url, check_media_limits=backend.check_media_limits,
                       UnsupportedSource=backend.UnsupportedSource, MediaTooLarge=backend.MediaTooLarge,
                       TRANSCRIBE_MAX_BYTES=backend.TRANSCRIBE_MAX_BYTES, TRANSCRIBE_MAX_SECONDS=backend.TRANSCRIBE_MAX_SECONDS)
            exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),scope)
            response=asyncio.run(scope['transcribe_audio'](
                file=UploadFile(BytesIO((self.root/filename).read_bytes()),filename=filename),
                url=None,request=None))
            self.assertEqual(response.status_code,status,response.body)
            self.assertEqual(client.audio.transcriptions.create.called,calls_api)
            self.assertEqual(database.called,calls_api)
            self.assertEqual(list(Path(uploads).iterdir()),[])
            if status==422:self.assertIn('no audio track',json.loads(response.body)['error'])


class FlagTests(unittest.TestCase):
    def test_enabled_values(self):
        for value in ('1', 'true', ' True ', 'YES', 'on'):
            with patch.dict(os.environ, {'CLIPFORGE_ENABLE_STUDIOS':value}):
                self.assertTrue(studios_enabled())

    def test_disabled_values(self):
        for value in ('', '0', 'false', 'invalid'):
            with patch.dict(os.environ, {'CLIPFORGE_ENABLE_STUDIOS':value}):
                self.assertFalse(studios_enabled())
