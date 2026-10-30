"""YouTube/TikTok links in Auto Clips, Thumbnails, Editor and Text-to-Clip."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from clipforge_studios import access
from clipforge_studios.backend import editor, link_media, openai_speech, server, studio
import url_media

LINK = 'https://youtu.be/SJywU4UQryQ'


class LinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.video = Path(cls.temp.name) / 'video.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:d=2', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=2', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def fake_fetch(self, link, destination):
        shutil.copy(self.video, destination); return Path(destination)

    def test_only_supported_public_links_are_downloaded(self):
        with patch.object(url_media, 'download') as download:
            with self.assertRaises(HTTPException) as caught:
                link_media.fetch('https://example.com/video.mp4', Path(self.temp.name) / 'x')
        self.assertEqual(caught.exception.status_code, 400)
        download.assert_not_called()

    def test_download_problems_become_clear_errors(self):
        for error, status in ((url_media.MediaTooLarge('Too long'), 413), (url_media.MediaError('Blocked'), 422)):
            with self.subTest(status=status), patch.object(url_media, 'check_transcribe_url'), \
                    patch.object(url_media, 'download', side_effect=error):
                with self.assertRaises(HTTPException) as caught:
                    link_media.fetch(LINK, Path(self.temp.name) / 'x')
                self.assertEqual(caught.exception.status_code, status)

    def test_successful_download_moves_to_the_destination_with_limits(self):
        temp = Path(self.temp.name) / 'downloaded.mp4'; shutil.copy(self.video, temp)
        target = Path(self.temp.name) / 'kept.mp4'
        with patch.object(url_media, 'check_transcribe_url'), patch.object(url_media, 'download', return_value=str(temp)) as download:
            self.assertEqual(link_media.fetch(LINK, target), target)
        download.assert_called_once_with(LINK, link_media.MAX_BYTES, link_media.MAX_SECONDS)
        self.assertTrue(target.is_file()); self.assertFalse(temp.exists())
        target.unlink()

    def test_auto_clips_thumbnails_and_editor_clips_get_a_source(self):
        host = FastAPI(); studio.register(host, lambda _: None)
        with patch.object(link_media, 'fetch', side_effect=self.fake_fetch):
            response = TestClient(host).post('/studio/sources/url', json=dict(link=LINK))
        self.assertEqual(response.status_code, 200, response.text)
        source = response.json()
        self.assertEqual(source['source_name'], 'youtu.be/SJywU4UQryQ')
        self.assertAlmostEqual(source['duration'], 2, delta=.2)
        stored = studio.SOURCES / (source['id'] + '.json')
        self.addCleanup(lambda: [p.unlink(missing_ok=True) for p in (stored, studio.SOURCES / (source['id'] + '.video'))])
        self.assertTrue(stored.is_file())
        # The page previews the downloaded video from the server; unknown sources are not found.
        http = TestClient(host)
        preview = http.get(f'/studio/sources/{source["id"]}/video')
        self.assertEqual((preview.status_code, preview.content), (200, self.video.read_bytes()))
        self.assertEqual(http.get('/studio/sources/' + 'f' * 32 + '/video').status_code, 404)

    def test_source_previews_are_owner_only_in_public_mode(self):
        self.assertEqual(access.resource_key('/studios-api/studio/sources/' + 'a' * 32 + '/video'), 'entity:' + 'a' * 32)
        self.assertIsNone(access.resource_key('/studio/sources/url'))

    def test_editor_library_gets_an_asset(self):
        host = FastAPI(); editor.register(host)
        with patch.object(link_media, 'fetch', side_effect=self.fake_fetch):
            response = TestClient(host).post('/editor/assets/url', json=dict(link=LINK))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['name'], 'youtu.be/SJywU4UQryQ')

    def test_text_to_clip_transcribes_the_link(self):
        transcript = dict(text='Hi there.', words=[dict(word='Hi', start=.1, end=.4), dict(word='there.', start=.5, end=.9)],
                          segments=[dict(first=0, last=1, text='Hi there.', start=.1, end=.9)], language='en',
                          engine='openai-whisper', model='whisper-1', alignment_version=3)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-test', 'CLIPFORGE_HEADLINE_PROVIDER': ''}), \
                patch.object(link_media, 'fetch', side_effect=self.fake_fetch), \
                patch.object(openai_speech, 'transcript', return_value=(transcript, 0.0002)):
            response = TestClient(server.app).post('/api/videos/url', json=dict(link=LINK))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()['source_name'], response.json()['text']), ('youtu.be/SJywU4UQryQ', 'Hi there.'))

    def test_link_field_is_not_mistaken_for_an_owned_file(self):
        # Public mode treats `url` as a reference to the caller's own files; links use `link`.
        checker = SimpleNamespace(owns=lambda key, user: False)
        checker.check_body = lambda value, user: access.Access.check_body(checker, value, user)
        access.Access.check_body(checker, dict(link=LINK), 'user')
        with self.assertRaises(HTTPException):
            access.Access.check_body(checker, dict(url=LINK), 'user')


if __name__ == '__main__':
    unittest.main()
