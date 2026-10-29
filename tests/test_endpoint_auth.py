"""Original app endpoints: sign-in, ownership, admin-only cookies, AI rate limits."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, mock_open, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
import app as backend
from rate_limit import RateLimiter

ALICE = dict(id='00000000-0000-4000-8000-00000000a11c', email='alice@example.invalid', email_verified=True)
BOB = dict(id='00000000-0000-4000-8000-0000000000b0', email='bob@example.invalid', email_verified=True)
OWNER = dict(id='00000000-0000-4000-8000-000000000001', email='owner@example.invalid', email_verified=True)
SESSIONS = {'alice': ALICE, 'bob': BOB, 'owner': OWNER}

def signed_in(request):
    header = request.headers.get('authorization', '')
    if not header.startswith('Bearer ') or header[7:] not in SESSIONS:
        raise HTTPException(401, 'Sign in to verify subscription access')
    return SESSIONS[header[7:]]

def as_user(name):
    return {'Authorization': 'Bearer ' + name}

def history_db(rows):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = rows
    db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = rows
    return db

def chat_reply(text):
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [Mock(message=Mock(content=text))]
    return client


class EndpointAuthTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(backend.app)
        for patcher in [patch.object(backend, 'OPENAI_API_KEY', 'test-key'),
                        patch.object(backend, 'client', chat_reply('fixture reply')),
                        patch.object(backend, 'ai_limiter', RateLimiter(1000, 600)),
                        patch.dict(os.environ, {'CLIPFORGE_OWNER_USER_ID': OWNER['id']})]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_anonymous_requests_are_rejected_before_any_work(self):
        # Real billing_actor: no Authorization header means 401.
        db, openai = history_db([{'id': 'r1'}]), Mock()
        with patch('db_history.get_db', return_value=db), patch.object(backend, 'OpenAI', openai), \
             patch.object(backend, 'client', openai), patch.object(backend, 'insert_transcript') as insert:
            calls = [
                ('get', '/history/alice@example.invalid', {}),
                ('post', '/history/update', dict(data={'record_id': 'r1', 'summary': 'x'})),
                ('post', '/save_ai_insights', dict(data={'record_id': 'r1', 'summary': 'x'})),
                ('post', '/transcribe', dict(data={'url': 'https://example.invalid/a.mp4'})),
                ('post', '/transcribe_clip', dict(data={'clip_url': 'a.mp4'})),
                ('post', '/ask-ai', dict(json={'prompt': 'hi'})),
                ('post', '/ai_chat', dict(data={'user_message': 'hi'})),
                ('post', '/data-upload', dict(files={'file': ('c.txt', b'# Netscape HTTP Cookie File\n')})),
            ]
            for method, path, kwargs in calls:
                with self.subTest(path=path):
                    self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 401)
        db.table.assert_not_called(); openai.assert_not_called(); insert.assert_not_called()

    def test_history_is_readable_only_by_its_owner(self):
        db = history_db([{'id': 'r1', 'user_id': ALICE['email']}])
        with patch.object(backend, 'billing_actor', signed_in), patch('db_history.get_db', return_value=db):
            other = self.client.get('/history/bob@example.invalid', headers=as_user('alice'))
            own = self.client.get('/history/ALICE@example.invalid', headers=as_user('alice'))
        self.assertEqual(other.status_code, 403)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.json()['history'], [{'id': 'r1', 'user_id': ALICE['email']}])
        db.table.return_value.select.return_value.eq.assert_called_once_with('user_id', ALICE['email'])

    def test_history_updates_only_touch_the_callers_rows(self):
        for path in ('/history/update', '/save_ai_insights'):
            with self.subTest(path=path):
                # Supabase returns no rows when id matches but user_id does not.
                not_owned, owned = history_db([]), history_db([{'id': 'r1'}])
                with patch.object(backend, 'billing_actor', signed_in):
                    with patch('db_history.get_db', return_value=not_owned):
                        denied = self.client.post(path, data={'record_id': 'r1', 'summary': 'x'}, headers=as_user('bob'))
                    with patch('db_history.get_db', return_value=owned):
                        allowed = self.client.post(path, data={'record_id': 'r1', 'summary': 'x'}, headers=as_user('alice'))
                self.assertEqual(denied.status_code, 404)
                not_owned.table.return_value.update.return_value.eq.return_value.eq.assert_called_once_with('user_id', BOB['email'])
                self.assertEqual(allowed.status_code, 200)
                self.assertEqual(allowed.json(), {'ok': True, 'updated': ['summary']})
                owned.table.return_value.update.assert_called_once_with({'summary': 'x'})
                owned.table.return_value.update.return_value.eq.return_value.eq.assert_called_once_with('user_id', ALICE['email'])

    def test_transcripts_are_saved_to_the_signed_in_account_not_the_form_user_id(self):
        with tempfile.TemporaryDirectory() as folder:
            mp3 = os.path.join(folder, 'audio.mp3')
            open(mp3, 'wb').close()
            openai = MagicMock()
            openai.audio.transcriptions.create.return_value = 'hello world'
            with patch.object(backend, 'billing_actor', signed_in), patch.object(backend, 'client', openai), \
                 patch.object(backend, 'audio_to_mp3', return_value=mp3), \
                 patch.object(backend, 'insert_transcript', return_value='r9') as insert:
                response = self.client.post('/transcribe', headers=as_user('alice'),
                                            data={'user_id': BOB['email']}, files={'file': ('a.mp4', b'video')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['text'], 'hello world')
        self.assertEqual(insert.call_args.kwargs['user_id'], ALICE['email'])

    def test_signed_in_users_can_use_ai_chat(self):
        with patch.object(backend, 'billing_actor', signed_in):
            response = self.client.post('/ai_chat', headers=as_user('alice'), data={'user_message': 'hi'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'reply': 'fixture reply'})

    def test_cookie_upload_is_admin_only(self):
        cookies = b'# Netscape HTTP Cookie File\n'
        with patch.object(backend, 'billing_actor', signed_in), \
             patch('builtins.open', mock_open(read_data=cookies)) as opened:
            customer = self.client.post('/data-upload', headers=as_user('alice'), files={'file': ('c.txt', cookies)})
            self.assertEqual(customer.status_code, 403)
            opened.assert_not_called()
            owner = self.client.post('/data-upload', headers=as_user('owner'), files={'file': ('c.txt', cookies)})
        self.assertEqual(owner.status_code, 200)
        self.assertTrue(owner.json()['ok'])


class AiRateLimitTests(unittest.TestCase):
    def test_sliding_window_blocks_then_frees_a_slot(self):
        now = [0.0]
        limiter = RateLimiter(2, 60, clock=lambda: now[0])
        self.assertEqual(limiter.hit('a'), 0)
        now[0] = 10
        self.assertEqual(limiter.hit('a'), 0)
        self.assertEqual(limiter.hit('a'), 50)
        self.assertEqual(limiter.hit('b'), 0)
        now[0] = 60.5
        self.assertEqual(limiter.hit('a'), 0)

    def test_ai_endpoints_return_429_per_user(self):
        with patch.object(backend, 'billing_actor', signed_in), patch.object(backend, 'OPENAI_API_KEY', 'test-key'), \
             patch.object(backend, 'client', chat_reply('ok')), patch.object(backend, 'OpenAI', return_value=chat_reply('ok')), \
             patch.object(backend, 'ai_limiter', RateLimiter(2, 600)):
            client = TestClient(backend.app)
            first = client.post('/ai_chat', headers=as_user('alice'), data={'user_message': 'one'})
            second = client.post('/ask-ai', headers=as_user('alice'), json={'prompt': 'two'})
            third = client.post('/ai_chat', headers=as_user('alice'), data={'user_message': 'three'})
            other_user = client.post('/ai_chat', headers=as_user('bob'), data={'user_message': 'one'})
        self.assertEqual([first.status_code, second.status_code], [200, 200])
        self.assertEqual(third.status_code, 429)
        self.assertIn('Too many AI requests', third.json()['detail'])
        self.assertGreater(int(third.headers['Retry-After']), 0)
        self.assertEqual(other_user.status_code, 200)


class MissingOpenAiKeyTests(unittest.TestCase):
    def test_ai_endpoints_return_503_with_a_clear_message(self):
        openai = Mock()
        with patch.object(backend, 'billing_actor', signed_in), patch.object(backend, 'OPENAI_API_KEY', ''), \
             patch.object(backend, 'client', None), patch.object(backend, 'OpenAI', openai):
            client = TestClient(backend.app)
            responses = [client.post('/ask-ai', headers=as_user('alice'), json={'prompt': 'hi'}),
                         client.post('/ai_chat', headers=as_user('alice'), data={'user_message': 'hi'})]
        for response in responses:
            self.assertEqual(response.status_code, 503)
            self.assertIn('OPENAI_API_KEY is not set', response.json()['detail'])
        openai.assert_not_called()


if __name__ == '__main__':
    unittest.main()
