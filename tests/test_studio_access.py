"""Local authentication/billing/ownership contracts; no production services."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import subprocess
import sys
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from clipforge_studios.access import Access,StudioAccess,BillingPool,verified_user

class AccessTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.records=[];self.allow=True;self.plan='trial';self.remaining=3
  def authenticate(token):
   if token not in ('local-test-a','local-test-b'):raise HTTPException(401,'Invalid test token')
   return dict(id=token,email=token+'@example.invalid')
  def access(email):return dict(allowed=self.allow,plan=self.plan,reason='trial_ok',clips_remaining=self.remaining-len([e for e in self.records if e==email]))
  self.access=Access(Path(self.temp.name)/'owners.sqlite3',authenticate,access,self.records.append,limit=lambda:3)
  app=FastAPI();self.pool=BillingPool(ThreadPoolExecutor(max_workers=1));self.addCleanup(self.pool.base.shutdown)
  self.gate=threading.Event();self.gate.set();self.addCleanup(self.gate.set)
  self.jobs={};self.worker_fails=False
  @app.post('/editor/assets')
  def asset():return dict(id=uuid.uuid4().hex,kind='video')
  @app.get('/editor/assets/{asset_id}')
  def read(asset_id:str):return dict(id=asset_id)
  def work(job):self.gate.wait(5);job['status']='failed' if self.worker_fails else 'completed'
  @app.post('/editor/jobs',status_code=202)
  def job(body:dict):
   job=dict(id=uuid.uuid4().hex,status='queued');self.jobs[job['id']]=job;self.pool.submit(work,job);return job
  @app.get('/editor/jobs/{job_id}')
  def state(job_id:str):return self.jobs[job_id]
  @app.get('/local-original/history')
  def history():return dict(records=[dict(id=k) for k in self.jobs])
  @app.post('/local-original/manual')
  def manual(body:dict):return dict(id=uuid.uuid4().hex,items=[{'preview_url':'/media/previews/'+uuid.uuid4().hex+'.mp4'}])
  @app.get('/media/previews/{file}')
  def media(file:str):return {'protected':True}
  host=FastAPI();host.mount('/studios-api',StudioAccess(app,self.access))
  self.http=TestClient(host)
 def headers(self,who='a'):return {'Authorization':'Bearer local-test-'+who}
 def upload(self,who='a'):return self.http.post('/studios-api/editor/assets',headers=self.headers(who)).json()['id']
 def wait(self,jid,who='a'):
  for _ in range(100):
   state=self.http.get('/studios-api/editor/jobs/'+jid,headers=self.headers(who)).json()
   if state.get('status') in ('completed','failed'):
    time.sleep(.02);return state
   time.sleep(.02)
  self.fail('Job did not finish')
 def test_no_session_and_forged_session_rejected(self):
  with patch.dict('os.environ',{'DEV_ALLOW':'1'}):self.assertEqual(self.http.post('/studios-api/editor/assets').status_code,401)
  self.assertEqual(self.http.post('/studios-api/editor/assets',headers={'Authorization':'Bearer forged'}).status_code,401)
 def test_other_user_cannot_read_or_render_asset(self):
  aid=self.upload()
  self.assertEqual(self.http.get('/studios-api/editor/assets/'+aid,headers=self.headers()).status_code,200)
  self.assertEqual(self.http.get('/studios-api/editor/assets/'+aid,headers=self.headers('b')).status_code,404)
  self.assertEqual(self.http.post('/studios-api/editor/jobs',headers=self.headers('b'),json={'segments':[{'asset_id':aid}]}).status_code,404)
 def test_cancelled_billing_blocks_render_not_original_modules(self):
  aid=self.upload();self.allow=False
  self.assertEqual(self.http.post('/studios-api/editor/jobs',headers=self.headers(),json={'segments':[{'asset_id':aid}]}).status_code,403)
  self.assertEqual(self.jobs,{})
 def test_pending_reservation_prevents_parallel_trial_bypass(self):
  aid=self.upload();self.remaining=1;self.gate.clear()
  r=self.http.post('/studios-api/editor/jobs',headers=self.headers(),json={'segments':[{'asset_id':aid}]});self.assertEqual(r.status_code,202,r.text)
  self.assertEqual(self.http.post('/studios-api/editor/jobs',headers=self.headers(),json={'segments':[{'asset_id':aid}]}).status_code,403)
  self.gate.set();self.wait(r.json()['id']);self.assertEqual(self.records,['local-test-a@example.invalid'])
  self.assertEqual(self.http.post('/studios-api/editor/jobs',headers=self.headers(),json={'segments':[{'asset_id':aid}]}).status_code,403)
 def test_failed_render_does_not_consume_usage(self):
  aid=self.upload();self.worker_fails=True
  r=self.http.post('/studios-api/editor/jobs',headers=self.headers(),json={'segments':[{'asset_id':aid}]});self.wait(r.json()['id'])
  self.assertEqual(self.records,[])
  self.assertEqual(self.http.post('/studios-api/editor/jobs',headers=self.headers(),json={'segments':[{'asset_id':aid}]}).status_code,202)
 def test_history_scoped_and_media_requires_owner(self):
  aid=self.upload();r=self.http.post('/studios-api/local-original/manual',headers=self.headers(),json={'source_id':aid});self.assertEqual(r.status_code,200,r.text)
  url=r.json()['items'][0]['preview_url']
  self.assertEqual(self.http.get('/studios-api'+url,headers=self.headers()).status_code,200)
  self.assertEqual(self.http.get('/studios-api'+url,headers=self.headers('b')).status_code,404)
  self.assertEqual(self.http.get('/studios-api/local-original/history',headers=self.headers('b')).json()['records'],[])
 def test_sdk_user_validation_uses_bearer_on_server(self):
  from types import SimpleNamespace
  uid=str(uuid.uuid4())
  with patch('clipforge_studios.access.auth_client') as client:
   client.return_value.auth.get_user.return_value=SimpleNamespace(user=SimpleNamespace(id=uid,email='verified@example.invalid'))
   actor=verified_user('local-contract-token');self.assertEqual(actor['id'],uid)
   client.return_value.auth.get_user.assert_called_once_with('local-contract-token')
 def test_billing_failure_fails_closed(self):
  self.access.access=lambda _:dict(allowed=True,plan='unknown',reason='db_unavailable')
  aid=self.upload()
  self.assertEqual(self.http.post('/studios-api/editor/jobs',headers=self.headers(),json={'segments':[{'asset_id':aid}]}).status_code,503)
 def test_core_starts_without_optional_speech_dependencies(self):
  script='''import importlib.abc, sys
class BlockOptional(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in ('numpy','faster_whisper'):raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0,BlockOptional())
import clipforge_studios.app
print('core-import-ok')
'''
  run=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
  self.assertEqual(run.returncode,0,run.stderr)

if __name__=='__main__':unittest.main(verbosity=2)
