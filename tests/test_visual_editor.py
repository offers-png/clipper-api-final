"""Visual Video Editing Workspace: timeline thumbnails, text fonts, and text drawn like the preview."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from clipforge_studios.backend import editor


class FrameTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); root = Path(temp.name)
        for name in ('ASSETS', 'FRAMES'):
            patcher = patch.object(editor, name, root / name.lower()); patcher.start(); self.addCleanup(patcher.stop)
            getattr(editor, name).mkdir()
        video = root / 'clip.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=30:duration=4',
                        '-f', 'lavfi', '-i', 'sine=d=4', '-shortest', '-pix_fmt', 'yuv420p', str(video)], check=True)
        app = FastAPI(); editor.register(app); self.http = TestClient(app)
        with video.open('rb') as f:
            self.video = self.http.post('/editor/assets', files={'file': ('clip.mp4', f)}).json()

    def test_frame_thumbnails_are_small_cached_jpegs(self):
        r = self.http.get(f'/editor/assets/{self.video["id"]}/frame?t=1.23&h=72')
        self.assertEqual((r.status_code, r.headers['content-type']), (200, 'image/jpeg'))
        self.assertTrue((editor.FRAMES / f'{self.video["id"]}_12_72.jpg').is_file())
        self.assertEqual(self.http.get(f'/editor/assets/{self.video["id"]}/frame?t=99').status_code, 200)  # past the end: last good frame

    def test_frames_only_for_videos_and_sane_sizes(self):
        self.assertEqual(self.http.get(f'/editor/assets/{self.video["id"]}/frame?h=5000').status_code, 404)
        self.assertEqual(self.http.get('/editor/assets/' + 'a' * 32 + '/frame').status_code, 404)


class TextTests(unittest.TestCase):
    def test_text_accepts_a_font_choice(self):
        t = editor.Text(text='Hi', end=2, font='Impact')
        self.assertEqual(t.font, 'Impact')
        with self.assertRaises(Exception):
            editor.Text(text='Hi', end=2, font='Comic Sans')
        self.assertEqual(editor.Text(text='Hi', end=2).font, 'Arial')

    @unittest.skipUnless(Path('C:/Windows/Fonts/impact.ttf').is_file(), 'needs the Windows fonts')
    def test_text_picture_has_the_box_color_and_a_color_emoji(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'text.png'
            self.assertTrue(editor.text_image(editor.Text(text='BEST PART 🔥', end=2, size=50, color='#ffd400', font='Impact'), path))
            with Image.open(path) as opened:
                image = opened.convert('RGBA')
            self.assertEqual(image.getpixel((2, 2)), (0, 0, 0, 115))  # the 45% black box with its 8 px border
            colors = image.getcolors(1 << 20)
            self.assertTrue(any(n > 50 and r > 240 and 200 < g < 220 and b < 20 for n, (r, g, b, a) in colors))  # #ffd400 text
            self.assertTrue(any(n > 20 and r > 200 and 60 < g < 160 and b < 60 for n, (r, g, b, a) in colors))  # orange flame
            two = Path(folder) / 'two.png'
            editor.text_image(editor.Text(text='A\nB', end=2, size=50), two)
            with Image.open(two) as taller:
                self.assertGreater(taller.height, image.height)  # each line adds a line


if __name__ == '__main__':
    unittest.main()
