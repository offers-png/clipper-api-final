"""Classic Best 3 Moments: OpenAI picks segment ranges; clip times are real segment times."""
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as backend
import best_moments
from tests.test_endpoint_auth import as_user, signed_in

# 6-second segments: 0-6, 6-12, ... 114-120
SEGMENTS = [dict(start=i * 6.0, end=i * 6.0 + 5.5, text=f'Sentence number {i}.') for i in range(20)]


class FakeChat:
    def __init__(self, clips):
        self.calls, self.clips = [], clips
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **options):
        self.calls.append(options)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
                               choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'clips': self.clips})))])


class PickTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'CLIPFORGE_HEADLINE_MODEL': ''}); env.start(); self.addCleanup(env.stop)

    def test_times_come_from_segments_not_the_model(self):
        client = FakeChat([dict(first_id=2, last_id=5, summary='Strong hook')])
        clips, usd = best_moments.pick(client, best_moments.clean(SEGMENTS), 3)
        self.assertEqual(clips, [dict(start='00:00:12', end='00:00:36', summary='Strong hook', seconds=23.5)])
        sent = json.loads(client.calls[0]['messages'][1]['content'])
        self.assertEqual(sent[2], dict(id=2, start=12.0, end=17.5, text='Sentence number 2.'))
        self.assertEqual(client.calls[0]['model'], 'gpt-4o-mini')
        self.assertTrue(client.calls[0]['response_format']['json_schema']['strict'])
        self.assertAlmostEqual(usd, (1000 * 0.15 + 100 * 0.60) / 1_000_000)

    def test_ranges_are_fitted_to_10_to_60_seconds(self):
        rows = best_moments.clean(SEGMENTS)
        self.assertEqual(best_moments.fit(rows, 0, 0), 1)     # 5.5 s grows to 11.5 s
        self.assertEqual(best_moments.fit(rows, 0, 19), 9)    # 119.5 s shrinks to 59.5 s

    def test_invalid_and_overlapping_picks_are_dropped(self):
        client = FakeChat([dict(first_id=2, last_id=5, summary='a'), dict(first_id=4, last_id=7, summary='overlaps'),
                           dict(first_id=50, last_id=51, summary='no such segment'), dict(first_id=9, last_id=8, summary='backwards'),
                           dict(first_id=10, last_id=12, summary='b'), dict(first_id=15, last_id=17, summary='c')])
        clips, _ = best_moments.pick(client, best_moments.clean(SEGMENTS), 2)
        self.assertEqual([c['summary'] for c in clips], ['a', 'b'])

    def test_unusable_segments_are_rejected(self):
        for bad in (None, [], 'text', [dict(start='x', end=1, text='a')], [dict(start=5, end=2, text='a')], [dict(start=0, end=1)]):
            with self.subTest(bad=bad):
                self.assertIsNone(best_moments.clean(bad))


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.http = TestClient(backend.app)
        for patcher in [patch.object(backend, 'OPENAI_API_KEY', 'test-key'), patch.object(backend, 'billing_actor', signed_in)]:
            patcher.start(); self.addCleanup(patcher.stop)

    def test_requires_sign_in(self):
        self.assertEqual(self.http.post('/auto_clip', json=dict(segments=SEGMENTS)).status_code, 401)

    def test_requires_timestamped_segments(self):
        response = self.http.post('/auto_clip', headers=as_user('alice'), json=dict(transcript='plain text only'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('Transcribe the full video first', response.json()['error'])

    def test_returns_clips_from_the_picker(self):
        with patch.object(backend, 'client', FakeChat([dict(first_id=0, last_id=2, summary='Opening')])):
            response = self.http.post('/auto_clip', headers=as_user('alice'), json=dict(segments=SEGMENTS, max_clips=3))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['clips'], [dict(start='00:00:00', end='00:00:18', summary='Opening', seconds=17.5)])

    def test_openai_failure_is_reported_without_details(self):
        broken = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(TimeoutError('secret')))))
        with patch.object(backend, 'client', broken):
            response = self.http.post('/auto_clip', headers=as_user('alice'), json=dict(segments=SEGMENTS))
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret', response.text)


if __name__ == '__main__':
    unittest.main()
