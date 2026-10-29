"""Local sign-in bypass: fake user only for explicit localhost requests, never on Render."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from starlette.requests import Request
from fastapi.testclient import TestClient
import dev_bypass
from dev_bypass import dev_actor, TOKEN
from billing_identity import billing_actor
from owner_access import development_access

RENDER_VARS = ('RENDER', 'RENDER_SERVICE_ID', 'RENDER_EXTERNAL_URL')


def request(client='127.0.0.1', host='localhost:8000', token=TOKEN, **extra):
    headers = {'host': host, **extra}
    if token is not None:
        headers['authorization'] = 'Bearer ' + token
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'client': (client, 50000) if client else None,
                    'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


class DevBypassTests(unittest.TestCase):
    def setUp(self):
        clean = {k: v for k, v in os.environ.items() if k not in RENDER_VARS + ('CLIPFORGE_DEV_BYPASS',)}
        patcher = patch.dict(os.environ, {**clean, 'CLIPFORGE_DEV_BYPASS': '1'}, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_local_request_with_setting_gets_the_fake_user(self):
        for client, host in [('127.0.0.1', 'localhost:8000'), ('::1', '[::1]:8000'), ('127.0.0.1', '127.0.0.1:8000')]:
            with self.subTest(host=host):
                actor = dev_actor(request(client, host))
                self.assertEqual(actor['email'], 'dev@localhost.test')
                self.assertTrue(actor['dev_bypass'])

    def test_every_guard_blocks_the_bypass_on_its_own(self):
        cases = {
            'setting off': (dict(CLIPFORGE_DEV_BYPASS='0'), request()),
            'setting missing': (dict(CLIPFORGE_DEV_BYPASS=''), request()),
            'running on Render': (dict(RENDER='true'), request()),
            'Render service id': (dict(RENDER_SERVICE_ID='srv-123'), request()),
            'remote client': ({}, request(client='203.0.113.9')),
            'private network client': ({}, request(client='192.168.1.20')),
            'no client address': ({}, request(client=None)),
            'proxied request': ({}, request(**{'X-Forwarded-For': '203.0.113.9'})),
            'forwarded header': ({}, request(Forwarded='for=203.0.113.9')),
            'public host name': ({}, request(host='clipper-api-final-1.onrender.com')),
            'DNS rebinding host': ({}, request(host='evil.example:8000')),
            'no token': ({}, request(token=None)),
            'wrong token': ({}, request(token='something-else')),
        }
        for name, (env, req) in cases.items():
            with self.subTest(name), patch.dict(os.environ, env):
                self.assertIsNone(dev_actor(req))

    def test_setting_can_come_from_the_backend_env_file_only(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLIPFORGE_DEV_BYPASS')
            env = Path(folder) / '.env'
            self.assertEqual(dev_bypass.setting(env), '')
            env.write_text('OTHER=1\nCLIPFORGE_DEV_BYPASS=1\n', encoding='utf-8')
            self.assertEqual(dev_bypass.setting(env), '1')

    def test_fake_user_is_used_without_contacting_supabase(self):
        with patch('billing_identity.auth_client', side_effect=AssertionError('must not call Supabase')):
            self.assertEqual(billing_actor(request())['email'], 'dev@localhost.test')

    def test_fake_user_gets_local_clipping_access_but_real_users_do_not(self):
        access = development_access(dev_actor(request()))
        self.assertTrue(access['allowed']); self.assertFalse(access['payment_verified'])
        self.assertIsNone(development_access(dict(id='00000000-0000-4000-8000-0000000000aa', email='real@example.invalid',
                                                  email_verified=True)))

    def test_routes_ignore_the_bypass_for_non_loopback_clients_and_block_checkout(self):
        import app as backend
        client = TestClient(backend.app)  # TestClient's peer address is 'testclient', not loopback
        headers = {'Authorization': 'Bearer ' + TOKEN}
        self.assertEqual(client.get('/dev/bypass', headers=headers).json()['enabled'], False)
        # Not bypassed: the token goes to the real Supabase check, which rejects it.
        with patch('billing_identity.auth_client', side_effect=ValueError('invalid token')):
            self.assertEqual(client.post('/billing/checkout', headers=headers).status_code, 401)
        with patch.object(backend, 'billing_actor', return_value=dev_actor(request())):
            response = client.post('/billing/checkout', headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertIn('not available in local test mode', response.json()['error'])


if __name__ == '__main__':
    unittest.main()
