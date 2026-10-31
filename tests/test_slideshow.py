"""Images + Voice -> Video: scenes at sentence ends, script timing, matching, review checks and the render."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from clipforge_studios.backend import slideshow


def sentence_words(sentences=12, per=8, step=.4, pause=.6):
    words, t = [], 0.0
    for s in range(sentences):
        for k in range(per):
            words.append(dict(word=f'w{s}_{k}' + ('.' if k == per - 1 else ''), start=round(t, 3), end=round(t + .3, 3)));t += step
        t += pause
    return words, round(t, 3)


def png(color, size=(320, 180)):
    from PIL import Image
    out = io.BytesIO();Image.new('RGB', size, color).save(out, 'PNG');return out.getvalue()


class SplitTests(unittest.TestCase):
    def test_one_scene_per_image_cut_at_sentence_ends(self):
        words, total = sentence_words()
        scenes = slideshow.split_scenes(words, 4, total)
        self.assertEqual(len(scenes), 4)
        self.assertEqual((scenes[0]['start'], scenes[-1]['end']), (0.0, total))  # covers the whole voiceover
        for a, b in zip(scenes, scenes[1:]):
            self.assertEqual(a['end'], b['start'])
            self.assertTrue(words[a['last']]['word'].endswith('.'))  # each cut follows a sentence end
            self.assertGreater(b['start'], words[a['last']]['end'])  # in the pause, not inside a word
        lengths = [s['end'] - s['start'] for s in scenes]
        self.assertLess(max(lengths) - min(lengths), 4.5)

    def test_more_images_than_sentences_uses_clauses_and_pauses(self):
        words, total = sentence_words(sentences=3, per=20)
        scenes = slideshow.split_scenes(words, 8, total)
        self.assertEqual(len(scenes), 8)
        self.assertTrue(all(s['end'] - s['start'] >= slideshow.MIN_SCENE for s in scenes))

    def test_script_supplies_the_words_and_heard_timing(self):
        heard = [dict(word='In', start=0, end=.2), dict(word='the', start=.3, end=.4), dict(word='name', start=.5, end=.8),
                 dict(word='of', start=.9, end=1.0), dict(word='Allah.', start=1.1, end=1.6)]
        words = slideshow.script_words('In the Name of God, Allah.', heard)
        self.assertEqual([w['word'] for w in words], ['In', 'the', 'Name', 'of', 'God,', 'Allah.'])
        self.assertEqual((words[2]['start'], words[5]['start']), (.5, 1.1))  # matched words keep the heard timing
        self.assertTrue(1.0 <= words[4]['start'] <= 1.1)  # an unheard word fits between its neighbours
        self.assertEqual(slideshow.script_words('', heard), heard)

    def test_file_name_order(self):
        names = ['10.png', '2.jpg', '01.png', '19.webp', '3.png']
        self.assertEqual(sorted(names, key=slideshow.natural), ['01.png', '2.jpg', '3.png', '10.png', '19.webp'])


class ReviewTests(unittest.TestCase):
    project = dict(images=[dict(id='a' * 32), dict(id='b' * 32)], audio=dict(duration=10.0))

    def check(self, scenes):
        slideshow.check_scenes(self.project, scenes)

    def test_valid_scenes(self):
        self.check([dict(image='b' * 32, start=0, end=4), dict(image='a' * 32, start=4, end=10)])  # swapped images are fine

    def test_invalid_scenes(self):
        for scenes, message in [
            ([dict(image='a' * 32, start=0, end=4), dict(image='a' * 32, start=4, end=10)], 'exactly once'),
            ([dict(image='a' * 32, start=0, end=4), dict(image='b' * 32, start=5, end=10)], 'gaps'),
            ([dict(image='a' * 32, start=0, end=4), dict(image='b' * 32, start=4, end=9)], 'whole voiceover'),
            ([dict(image='a' * 32, start=0, end=.5), dict(image='b' * 32, start=.5, end=10)], 'at least')]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.check(scenes)

    def test_ken_burns_alternates(self):
        self.assertIn("z='1+0.08*", slideshow.ken_burns(0, 300, 1920, 1080))
        self.assertIn("z='1.08-0.08*", slideshow.ken_burns(1, 300, 1920, 1080))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root = Path(temp.name)
        for patcher in (patch.object(slideshow, 'ROOT', self.root), patch.object(slideshow, 'PROJECTS', {}),
                        patch.object(slideshow, 'openai_enabled', return_value=True)):
            patcher.start();self.addCleanup(patcher.stop)
        self.audio = self.root / 'voice.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=330:duration=9.6', str(self.audio)], check=True)
        self.words, _ = sentence_words(sentences=3, per=6, step=.45, pause=.5)
        app = FastAPI();slideshow.register(app);self.http = TestClient(app)

    def upload(self, script=''):
        files = [('audio', ('voice.wav', self.audio.read_bytes(), 'audio/wav'))]
        files += [('images', (name, png(color), 'image/png')) for name, color in (('10.png', 'red'), ('2.png', 'green'), ('1.png', 'blue'))]
        with patch.object(slideshow.openai_speech, 'words', return_value=[dict(w) for w in self.words]):
            project = self.http.post('/slideshow/projects', files=files, data=dict(script=script)).json()
            for _ in range(100):
                project = self.http.get('/slideshow/projects/' + project['id']).json()
                if project['status'] != 'transcribing':break
                time.sleep(.05)
        return project

    def test_upload_transcribes_and_puts_images_in_file_order(self):
        project = self.upload()
        self.assertEqual(project['status'], 'ready')
        self.assertEqual([i['name'] for i in project['images']], ['1.png', '2.png', '10.png'])
        self.assertEqual([s['image'] for s in project['scenes']], [i['id'] for i in project['images']])
        self.assertNotIn('path', project['audio'])
        self.assertEqual(project['cost_usd']['transcription'], round(9.6 / 60 * .006, 4))
        thumb = self.http.get(f'/slideshow/projects/{project["id"]}/images/{project["images"][0]["id"]}?thumb=1')
        self.assertEqual((thumb.status_code, thumb.headers['content-type']), (200, 'image/jpeg'))

    def test_ai_match_needs_the_confirmed_quote_and_uses_each_image_once(self):
        project = self.upload()
        url = f'/slideshow/projects/{project["id"]}/match'
        self.assertEqual(self.http.post(url, json=dict(mode='ai')).status_code, 409)
        reply = lambda content: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)
        def create(**options):
            if options.get('response_format'):  # the matcher picks image 2 twice: repaired to one each
                return reply(json.dumps(dict(matches=[dict(scene=0, image=2), dict(scene=1, image=2), dict(scene=2, image=0)])))
            return reply('A colored square.')
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        quote = self.http.get(url + '-quote').json()['usd']
        with patch('openai.OpenAI', return_value=fake):
            matched = self.http.post(url, json=dict(mode='ai', confirmed_usd=quote)).json()
        ids = [i['id'] for i in matched['images']]
        self.assertEqual([s['image'] for s in matched['scenes']], [ids[2], ids[1], ids[0]])

    def test_render_matches_the_voiceover_length(self):
        project = self.upload()
        scenes = [dict(image=s['image'], start=s['start'], end=s['end']) for s in project['scenes']]
        scenes[0]['image'], scenes[1]['image'] = scenes[1]['image'], scenes[0]['image']  # a swap from the review screen
        with patch.object(slideshow, 'SIZES', {'16:9': (320, 180), '9:16': (180, 320)}):
            job = self.http.post(f'/slideshow/projects/{project["id"]}/renders', json=dict(scenes=scenes, ratio='16:9', captions=True)).json()
            for _ in range(600):
                job = self.http.get(f'/slideshow/projects/{project["id"]}/renders/{job["id"]}').json()
                if job['status'] != 'running':break
                time.sleep(.2)
        self.assertEqual(job['status'], 'completed', job.get('error'))
        self.assertAlmostEqual(job['duration'], 9.6, delta=.05)  # ends when the audio ends
        video = self.http.get(job['url'])
        self.assertEqual((video.status_code, video.headers['content-type']), (200, 'video/mp4'))


if __name__ == '__main__':
    unittest.main()
