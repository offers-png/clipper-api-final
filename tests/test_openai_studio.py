"""Auto Clips and Text-to-Clip with OpenAI when a key is set; local models only without one."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from clipforge_studios.backend import openai_analysis, openai_speech, server, studio, studio_ai

KEY = {'OPENAI_API_KEY': 'sk-test', 'CLIPFORGE_HEADLINE_PROVIDER': '', 'CLIPFORGE_HEADLINE_MODEL': ''}


def option(i):
    return dict(id=i, text=f'Clip {i} explains why sleep matters for memory and focus today.', start=i * 30.0, end=i * 30.0 + 30)


class FakeChat:
    """Scores every candidate it is sent; ids from `drop` are left out."""
    def __init__(self, reply=None):
        self.calls, self.reply = [], reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **options):
        self.calls.append(options)
        sent = json.loads(options['messages'][1]['content'])
        moments = self.reply(sent) if self.reply else [dict(id=c['id'], suitable=True, score=c['id'] / 100, evidence='why sleep matters for memory',
                                                             reason='r', hook_quote='sleep matters for memory', context_quote='', emoji='idea') for c in sent]
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500),
                               choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'moments': moments})))])


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, KEY); env.start(); self.addCleanup(env.stop)

    def test_scores_in_batches_with_the_local_result_shape(self):
        client = FakeChat(); seen = []
        items, usd = openai_analysis.analyse([option(i) for i in range(85)], 'best', seen.append, client=client)
        self.assertEqual(len(client.calls), 3)  # 40 + 40 + 5
        self.assertEqual(seen, [1 / 3, 2 / 3, 1.0])
        self.assertEqual(items[0], dict(id=84, suitable=True, score=.84, evidence='why sleep matters for memory', reason='r',
                                         hook='sleep matters for memory', context='', emoji='idea', engine='OpenAI gpt-4o-mini'))
        self.assertEqual([i['id'] for i in items], list(range(84, -1, -1)))
        self.assertAlmostEqual(usd, 3 * (1000 * .15 + 500 * .60) / 1_000_000)
        self.assertIn(studio_ai.CRITERIA['best'], client.calls[0]['messages'][0]['content'])

    def test_same_checks_as_the_local_model(self):
        def reply(sent):
            return [dict(id=0, suitable=True, score=.9, evidence='not in the text at all', reason='r', hook_quote='invented hook words here', context_quote='', emoji='idea'),
                    dict(id=1, suitable=True, score=1.7, evidence='why sleep matters', reason='r', hook_quote='', context_quote='', emoji=''),
                    dict(id=99, suitable=True, score=.9, evidence='why sleep matters', reason='r', hook_quote='', context_quote='', emoji=''),
                    dict(id=2, suitable='yes', score=.9, evidence='why sleep matters', reason='r', hook_quote='', context_quote='', emoji='')]
        items, _ = openai_analysis.analyse([option(i) for i in range(3)], 'best', client=FakeChat(reply))
        self.assertEqual(len(items), 1)  # bad score, unknown id and non-boolean suitable are dropped
        self.assertEqual(items[0]['evidence'], studio_ai.source_quotes(option(0)['text'])[0])  # ungrounded -> real words
        self.assertEqual(items[0]['hook'], '')

    def test_limit_asks_for_only_the_best(self):
        client = FakeChat()
        openai_analysis.analyse([option(i) for i in range(5)], 'funny', limit=4, client=client)
        self.assertIn('only for the 4 candidates that fit best', client.calls[0]['messages'][0]['content'])


class SpeechTests(unittest.TestCase):
    WORDS = [dict(word='Hello', start=0.0, end=0.4), dict(word='there.', start=0.5, end=0.5),
             dict(word='Why', start=1.0, end=1.2), dict(word='not?', start=1.3, end=1.6)]

    def test_transcript_has_the_local_shape_and_no_zero_length_words(self):
        with patch.object(openai_speech, 'words', return_value=[dict(w) for w in self.WORDS]):
            data, usd = openai_speech.transcript('video.mp4', 120)
        self.assertEqual(data['engine'], 'openai-whisper')
        self.assertEqual(data['words'][1]['end'], 0.52)
        self.assertEqual(data['segments'], [dict(first=0, last=1, text='Hello there.', start=0.0, end=0.52),
                                            dict(first=2, last=3, text='Why not?', start=1.0, end=1.6)])
        self.assertEqual(usd, 0.012)

    def test_source_words_are_kept_and_not_billed_twice(self):
        source = dict(source='video.mp4', duration=60.0)
        with patch.object(openai_speech, 'has_audio', return_value=True), patch.object(openai_speech, 'words', return_value=self.WORDS) as call:
            self.assertEqual(openai_speech.source_words(source), (self.WORDS, 0.006))
            self.assertEqual(openai_speech.source_words(source), (self.WORDS, 0))
        call.assert_called_once()


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        host = FastAPI(); studio.register(host, lambda _: None); self.http = TestClient(host)

    def test_openai_makes_highlights_available_without_local_models(self):
        with patch.dict(os.environ, KEY), patch.object(studio_ai, 'status', return_value=dict(ready=False, model='qwen')):
            data = self.http.get('/studio/capabilities').json()
        self.assertTrue(data['ai']['ready'])
        self.assertEqual(data['ai']['engine'], 'OpenAI gpt-4o-mini')
        self.assertEqual(data['transcription']['engine'], 'OpenAI whisper-1')

    def test_without_a_key_the_local_status_is_reported(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}), patch.object(studio_ai, 'status', return_value=dict(ready=False, model='qwen')):
            data = self.http.get('/studio/capabilities').json()
        self.assertFalse(data['ai']['ready'])
        self.assertNotIn('engine', data['ai'])

    def test_auto_clips_words_come_from_openai_with_their_cost(self):
        job, source = dict(id='a' * 32, warnings=[]), dict(id='b' * 32, source='video.mp4', duration=600.0)
        with patch.dict(os.environ, KEY), patch.object(studio, 'update'), \
                patch.object(openai_speech, 'source_words', return_value=(SpeechTests.WORDS, 0.06)), \
                patch.object(studio, 'transcribe', side_effect=AssertionError('local model must not run')):
            self.assertEqual(studio._words(source, job), SpeechTests.WORDS)
        self.assertEqual((job['cost_usd'], job['transcription_engine']), ({'transcription': 0.06}, 'OpenAI whisper-1'))


class TextToClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.video = Path(cls.temp.name) / 'video.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:d=2', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=2', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_upload_uses_openai_and_the_transcript_can_be_clipped(self):
        transcript = dict(text='Hello there.', words=[dict(word='Hello', start=.1, end=.5), dict(word='there.', start=.6, end=1.0)],
                          segments=[dict(first=0, last=1, text='Hello there.', start=.1, end=1.0)], language='en',
                          engine='openai-whisper', model='whisper-1', alignment_version=3)
        http = TestClient(server.app)
        with patch.dict(os.environ, KEY), patch.object(openai_speech, 'transcript', return_value=(transcript, 0.0002)), \
                patch.object(server, 'transcribe', side_effect=AssertionError('local model must not run')), \
                patch.object(server, 'MODEL_PATH', Path('missing-local-model')):
            with self.video.open('rb') as video:
                uploaded = http.post('/api/videos', files={'file': ('video.mp4', video, 'video/mp4')})
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            self.assertEqual((uploaded.json()['engine'], uploaded.json()['cost_usd']), ('openai-whisper', 0.0002))
            clip = http.post(f'/api/videos/{uploaded.json()["video_id"]}/selection', json=dict(first=0, last=1))
        self.assertEqual(clip.status_code, 200, clip.text)
        self.assertEqual(clip.json()['text'], 'Hello there.')


if __name__ == '__main__':
    unittest.main()
