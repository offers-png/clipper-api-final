"""Simulated KIE HTTP only. Fixture MP4s are not evidence of AI synthesis."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from . import test_kie_account as account_fixtures
from .test_kie_account import CreditsTransport
from clipforge_studios.backend import image_video as video,kie_provider as kie,kie_account as policy,editor,video_models

class GeneratorTests(unittest.TestCase):
 setUpClass=classmethod(account_fixtures.AccountTests.setUpClass.__func__)
 tearDownClass=classmethod(account_fixtures.AccountTests.tearDownClass.__func__)
 setUp=account_fixtures.AccountTests.setUp
 def workflow(self,mode,version,duration=5,ratio='16:9'):
  with tempfile.TemporaryDirectory() as temp:
   folder=Path(temp);mp4=folder/'fixture.mp4'
   subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',('color=c=blue:s=1280x720:r=24' if ratio=='16:9' else 'color=c=blue:s=720x1280:r=24'),'-t',str(duration),'-c:v','libx264','-pix_fmt','yuv420p',str(mp4)],check=True)
   transport=CreditsTransport(self.image,video=mp4.read_bytes(),states=['success'])
   provider=kie.KieVideoProvider(kie.KieClient('contract-test',session=transport,sleep=lambda _:None))
   with patch.object(editor,'ASSETS',folder),patch.object(editor,'JOBS',folder),patch.object(video,'provider',provider):
    image=editor.save_asset(self.image_path,'Actual fixture image')
    body=video.Generation(mode=mode,model=f'wan/{version}-{mode}-to-video',image_id=image['id'] if mode=='image' else None,prompt='A cat walks slowly.',motion='Custom prompt',duration=duration,ratio=ratio)
    video.validate(body);binding=video.binding(body);quote=policy.quote('video',binding,1000)
    qid=policy.reserve(quote['quote_id'],'video',binding,1000,True)
    job=dict(id='a'*32,paid_quote_id=qid,status='queued');video.worker(job,body)
    self.assertEqual(job['status'],'completed',job)
    self.assertTrue((folder/job['id']/'finished.mp4').is_file());self.assertEqual(transport.task_count,1)
    payload=next(kwargs['json'] for method,url,kwargs in transport.calls if url.endswith('/createTask'))
    self.assertEqual(payload['model'],body.model)
    inputs=payload['input'];self.assertEqual(inputs['resolution'],'720p')
    self.assertEqual(inputs['duration'],str(duration) if version=='2-5' else duration)
    if mode=='text':
     self.assertNotIn('first_frame_url',inputs);self.assertNotIn('image_url',inputs)
     self.assertFalse(any(url==kie.UPLOAD for _,url,_ in transport.calls))
    else:self.assertIn('image_url' if version=='2-5' else 'first_frame_url',inputs)
 def test_wan25_text_ten_seconds_portrait(self):self.workflow('text','2-5',10,'9:16')
 def test_wan25_image_ten_seconds_portrait(self):self.workflow('image','2-5',10,'9:16')
 def test_wan25_text(self):self.workflow('text','2-5')
 def test_wan25_image(self):self.workflow('image','2-5')
 def test_wan27_text(self):self.workflow('text','2-7')
 def test_wan27_image(self):self.workflow('image','2-7')
 def test_exact_quotes_and_cap_for_ten_seconds(self):
  for mode in ['text','image']:
   for version,credits,allowed in [('2-5',120,True),('2-7',160,False)]:
    q=policy.quote('video',dict(mode=mode,model=f'wan/{version}-{mode}-to-video',duration=10,ratio='9:16'),1000)
    self.assertEqual(q['cost_credits'],credits);self.assertEqual(q['can_submit'],allowed)
 def test_incompatible_or_unknown_model_is_rejected(self):
  for model in ['unknown','wan/2-5-image-to-video']:
   with self.assertRaises(Exception):video_models.resolve(dict(model=model,mode='text',duration=5))
 def test_api_requires_confirmation_and_does_not_submit_on_quote(self):
  transport=CreditsTransport(self.image,states=['success']);provider=kie.KieVideoProvider(kie.KieClient('contract-test',session=transport))
  app=FastAPI();video.register(app);client=TestClient(app)
  body=dict(mode='text',model='wan/2-5-text-to-video',prompt='A cat walks.',duration=5,ratio='16:9')
  with patch.object(video,'provider',provider),patch.object(video,'STATE',{}):
   q=client.post('/video-generator/quote',json=body);self.assertEqual(q.status_code,200)
   r=client.post('/video-generator/jobs',json=body);self.assertEqual(r.status_code,428,r.text)
  self.assertEqual(transport.task_count,0)
 def test_quote_prevents_model_and_ratio_swap(self):
  binding=dict(mode='text',model='wan/2-5-text-to-video',duration=5,ratio='16:9')
  q=policy.quote('video',binding,1000);qid=policy.reserve(q['quote_id'],'video',binding,1000,True)
  with policy.execution(qid):
   with self.assertRaises(RuntimeError):policy.claim_submission('wan/2-5-text-to-video',dict(prompt='A cat',duration='5',resolution='720p',aspect_ratio='9:16',enable_prompt_expansion=False),1000)
