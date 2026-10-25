import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from clipforge_studios.install import install
from clipforge_studios.backend.video_provider_config import load
from clipforge_studios.backend.image_video import Unavailable

ROOT=Path(__file__).resolve().parents[1]

class ConfigurationTests(unittest.TestCase):
    def test_install_disabled_by_default(self):
        host=FastAPI();before=list(host.routes)
        with patch.dict(os.environ,{'CLIPFORGE_ENABLE_STUDIOS':'0'}):self.assertFalse(install(host))
        self.assertEqual(host.routes,before)

    def test_local_install_and_remote_rejection(self):
        host=FastAPI()
        with patch.dict(os.environ,{'CLIPFORGE_ENABLE_STUDIOS':'1'}):self.assertTrue(install(host,local_only=True))
        self.assertEqual(TestClient(host).get('/studios-api/health').status_code,200)
        self.assertEqual(TestClient(host,client=('198.51.100.1',1234)).get('/studios-api/health').status_code,403)

    def test_unset_provider_is_explicitly_unavailable(self):
        with patch.dict(os.environ,{'CLIPFORGE_VIDEO_PROVIDER_FACTORY':''}):
            self.assertFalse(load(Unavailable()).status()['available'])

    def test_server_env_is_forwarded_only_to_factory(self):
        module=types.ModuleType('contract_video_adapter');calls=[]
        def factory(**kwargs):calls.append(kwargs);return Unavailable()
        module.factory=factory
        env={'CLIPFORGE_VIDEO_PROVIDER_FACTORY':'contract_video_adapter:factory','CLIPFORGE_VIDEO_PROVIDER_API_KEY':'contract-test','CLIPFORGE_VIDEO_PROVIDER_CONFIG':'{"quality":"test"}'}
        with patch.dict(sys.modules,{'contract_video_adapter':module}),patch.dict(os.environ,env):
            provider=load(Unavailable())
        self.assertEqual(calls,[dict(api_key='contract-test',config={'quality':'test'})])
        self.assertNotIn('contract-test',json.dumps(provider.status()))

    def test_invalid_provider_fails_closed_without_exception_details(self):
        module=types.ModuleType('broken_video_adapter')
        def factory(**kwargs):raise RuntimeError('contract-test private diagnostic')
        module.factory=factory
        with patch.dict(sys.modules,{'broken_video_adapter':module}),patch.dict(os.environ,{'CLIPFORGE_VIDEO_PROVIDER_FACTORY':'broken_video_adapter:factory'}):
            status=load(Unavailable()).status()
        self.assertFalse(status['available']);self.assertNotIn('private diagnostic',json.dumps(status))

    def test_original_production_modules_unchanged(self):
        for name in ('auth.py','billing.py','stripe_billing.py','db.py','db_history.py','render.yaml','Dockerfile','requirements.txt'):
            before=subprocess.check_output(['git','show','origin/main:'+name],cwd=ROOT).decode().replace('\r\n','\n')
            self.assertEqual((ROOT/name).read_text(encoding='utf-8').replace('\r\n','\n'),before,name)
        original=subprocess.check_output(['git','show','origin/main:app.py'],cwd=ROOT).decode().replace('\r\n','\n')
        self.assertTrue((ROOT/'app.py').read_text(encoding='utf-8').startswith(original))

if __name__=='__main__':unittest.main(verbosity=2)
