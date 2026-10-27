"""All provider HTTP is simulated; no live or paid generation in this suite."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
import os
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException,FastAPI
from fastapi.testclient import TestClient
from clipforge_studios.backend import kie_account as policy,kie_budget as legacy,kie_provider as kie
from clipforge_studios.backend import image_video,editor
from tests.test_kie_provider import Response,Transport,CDN

USER='00000000-0000-4000-8000-000000000001'
OTHER='00000000-0000-4000-8000-000000000002'
INPUT=dict(prompt='Subtle movement Subtle natural movement.',first_frame_url=CDN+'/reference.png',duration=5,resolution='720p',prompt_extend=False)
BINDING=dict(image_id='a'*32,duration=5,ratio='16:9',motion='Natural movement',prompt='Subtle movement')

class CreditsTransport(Transport):
 def request(self,method,url,**kwargs):
  if url=='https://api.kie.ai/api/v1/chat/credit':
   self.calls.append((method,url,kwargs));return Response(dict(code=200,data=1000))
  return super().request(method,url,**kwargs)

class AccountTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
  cls.image_path=cls.root/'fixture.png'
  subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=256x256','-frames:v','1',str(cls.image_path)],check=True)
  cls.image=cls.image_path.read_bytes()
 @classmethod
 def tearDownClass(cls):cls.temp.cleanup()
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  folder=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
  self.stack.enter_context(patch.object(policy,'LEDGER',folder/'account.sqlite3'))
  self.stack.enter_context(patch.object(legacy,'LEDGER',folder/'legacy.sqlite3'))
  self.stack.enter_context(patch.dict(os.environ,{'CLIPFORGE_KIE_ACCOUNT_MODE':'1','CLIPFORGE_KIE_ALLOWED_USER_IDS':USER,'CLIPFORGE_KIE_SPENDING_LIMIT_USD':'1','CLIPFORGE_KIE_ALLOW_PAID_REQUESTS':'0','CLIPFORGE_KIE_TEST_USER_ID':USER}))
  token=legacy.actor.set(dict(id=USER,email='fixture@example.invalid'));self.addCleanup(legacy.actor.reset,token)
  with legacy.database() as db:db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?)',('prior-test',USER,'submitted',40,80,'fixture-prior',0))
  self.stack.enter_context(patch('requests.sessions.Session.request',side_effect=AssertionError('LIVE HTTP FORBIDDEN')))
  self.stack.enter_context(patch.object(kie,'public_dns',side_effect=lambda url:kie.safe_url(url)))
 def quote(self,kind='video',binding=None,balance=1000):return policy.quote(kind,binding or BINDING,Decimal(balance))
 def reserve(self,q,kind='video',binding=None,balance=1000):return policy.reserve(q['quote_id'],kind,binding or BINDING,Decimal(balance),True)
 def test_enabled_account_counts_prior_spending_without_global_paid_flag(self):
  s=policy.status(True)
  self.assertTrue(s['available']);self.assertEqual(s['committed_usd'],'0.400');self.assertEqual(s['remaining_usd'],'0.600')
  self.assertEqual(self.quote('images',dict(job_id='b'*32))['cost_credits'],12)
 def test_other_accounts_and_missing_actor_are_blocked(self):
  for actor in (None,dict(id=OTHER)):
   token=legacy.actor.set(actor)
   try:
    self.assertFalse(policy.status(True)['available'])
    with self.assertRaises(HTTPException):self.quote()
   finally:legacy.actor.reset(token)
 def test_zero_balance_quotes_without_submission_or_reservation(self):
  q=self.quote(balance=0);self.assertFalse(q['can_submit']);self.assertIn('insufficient',q['reason'])
  with self.assertRaises(HTTPException):self.reserve(q,balance=0)
  self.assertEqual(policy.status(True)['remaining_usd'],'0.600')
 def test_confirmation_required_and_binding_cannot_change(self):
  q=self.quote()
  with self.assertRaises(HTTPException) as e:policy.reserve(q['quote_id'],'video',BINDING,1000,False)
  self.assertEqual(e.exception.status_code,428)
  with self.assertRaises(HTTPException):self.reserve(q,binding={**BINDING,'duration':15})
 def test_expired_quote_cannot_spend(self):
  q=self.quote()
  with policy.database() as db:db.execute('UPDATE quotes SET expires=0 WHERE id=?',(q['quote_id'],))
  with self.assertRaises(HTTPException):self.reserve(q)
 def test_concurrent_confirmations_make_one_reservation(self):
  q=self.quote()
  def run(_):
   token=legacy.actor.set(dict(id=USER))
   try:self.reserve(q);return 1
   except HTTPException:return 0
   finally:legacy.actor.reset(token)
  with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(run,range(12))),1)
  self.assertEqual(policy.status(True)['committed_usd'],'0.800')
 def test_budget_and_pending_job_cannot_be_bypassed(self):
  q=self.quote();qid=self.reserve(q);other=self.quote()
  with self.assertRaises(HTTPException):self.reserve(other)
  with policy.execution(qid):policy.claim_submission(policy.VIDEO_MODEL,INPUT,1000);policy.terminal('success')
  policy.finish(qid,True)
  with self.assertRaises(HTTPException) as e:self.reserve(other)
  self.assertEqual(e.exception.status_code,403)
 def test_unused_reservation_is_released_without_reusing_quote(self):
  q=self.quote();qid=self.reserve(q);policy.finish(qid,False)
  self.assertEqual(policy.status(True)['remaining_usd'],'0.600')
  with self.assertRaises(HTTPException):self.reserve(q)
 def test_uncertain_submission_blocks_new_paid_jobs_without_retry(self):
  class Failure:
   calls=0
   def request(self,method,url,**kwargs):
    if url.endswith('/credit'):return Response(dict(code=200,data=1000))
    self.calls+=1;raise RuntimeError('contract-test')
  t=Failure();c=kie.KieClient('contract-test',session=t);q=self.quote();qid=self.reserve(q)
  with policy.execution(qid):
   with self.assertRaises(kie.KieError):c.task(policy.VIDEO_MODEL,INPUT)
   with self.assertRaises(RuntimeError):c.task(policy.VIDEO_MODEL,INPUT)
  self.assertEqual(t.calls,1);policy.finish(qid,False)
  with self.assertRaises(HTTPException):self.reserve(self.quote())
 def test_unquoted_parameters_and_model_are_rejected_before_http(self):
  q=self.quote();qid=self.reserve(q)
  with policy.execution(qid):
   for model,inputs in [(policy.IMAGE_MODEL,INPUT),(policy.VIDEO_MODEL,{**INPUT,'resolution':'1080p'}),(policy.VIDEO_MODEL,{**INPUT,'duration':15})]:
    with self.assertRaises(RuntimeError):policy.claim_submission(model,inputs,1000)
 def test_three_image_contract_uses_exactly_three_tasks_and_no_key_in_results(self):
  q=self.quote('images',dict(job_id='b'*32));qid=self.reserve(q,'images',dict(job_id='b'*32))
  t=CreditsTransport(self.image,states=['success']);c=kie.KieClient('contract-test',session=t,sleep=lambda _:None)
  with policy.execution(qid):
   for i in range(3):self.assertEqual(c.generate_image('Concept '+str(i),references=[self.image_path]),self.image)
   with self.assertRaises(RuntimeError):c.generate_image('Fourth task blocked',references=[self.image_path])
  self.assertEqual(t.task_count,3);policy.finish(qid,True)
  self.assertEqual(policy.status(True)['committed_usd'],'0.460')
  self.assertNotIn('contract-test',str(q))
 def test_credit_request_is_read_only_and_errors_do_not_leak_key(self):
  class Bad:
   calls=[]
   def request(self,method,url,**kwargs):self.calls.append((method,url));return Response(dict(code=401,msg='contract-test'))
  transport=Bad();client=kie.KieClient('contract-test',session=transport)
  with self.assertRaises(kie.KieError) as e:client.credit_balance()
  self.assertNotIn('contract-test',str(e.exception))
  self.assertEqual(transport.calls,[('GET','https://api.kie.ai/api/v1/chat/credit')])
 def test_api_quote_checks_credits_but_does_not_generate(self):
  with tempfile.TemporaryDirectory() as temp:
   assets=Path(temp);t=CreditsTransport(self.image,states=['success']);p=kie.KieVideoProvider(kie.KieClient('contract-test',session=t))
   with patch.object(editor,'ASSETS',assets),patch.object(image_video,'provider',p):
    asset=editor.save_asset(self.image_path,'fixture')
    host=FastAPI();image_video.register(host);http=TestClient(host)
    body={**BINDING,'image_id':asset['id']}
    result=http.post('/image-video/quote',json=body)
    self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['cost_credits'],80)
    result=http.post('/image-video/jobs',json={**body,'quote_id':result.json()['quote_id']})
    self.assertEqual(result.status_code,428,result.text);self.assertEqual(t.task_count,0)
    self.assertTrue(all(method=='GET' for method,url,options in t.calls))

if __name__=='__main__':unittest.main()
