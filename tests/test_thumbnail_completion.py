"""Thumbnail safety/configuration contracts; not evidence of AI image synthesis."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from clipforge_studios.backend import thumbnail_ai as thumb, studio_ai, studio_memory, thumbnail_vision

class ThumbnailTests(unittest.TestCase):
 def setUp(self):
  self.words=[dict(word=word,start=i*.3,end=(i+1)*.3) for i,word in enumerate('First real sentence. Second real sentence. Third real sentence.'.split())]
 def test_phrases_retain_actual_word_boundaries(self):
  rows=thumb.phrases(self.words)
  self.assertEqual(len(rows),3)
  self.assertEqual(rows[1]['text'],'Second real sentence.')
  self.assertAlmostEqual(rows[1]['start'],.9);self.assertAlmostEqual(rows[1]['end'],1.8)
 def test_concepts_receive_exact_source_time_and_end(self):
  phrases=thumb.phrases(self.words)
  concepts=[dict(title=p['text'],evidence=p['text'],rationale='Fixture only',image_prompt='Fixture only') for p in reversed(phrases)]
  replies=iter([{**c,'topic':'Fixture'} for c in concepts])
  def request(path,body=None):return {'tokens':[1]} if path=='/tokenize' else {'content':json.dumps(next(replies))}
  with patch.object(studio_ai,'_start'),patch.object(studio_ai,'shutdown'),patch.object(studio_ai,'_request',side_effect=request):
   result=thumb.plan(self.words)
  for c,p in zip(result['concepts'],reversed(phrases)):
   self.assertEqual((c['time'],c['end']),(p['start'],p['end']))
 def test_ungrounded_evidence_never_defaults_to_zero(self):
  c=dict(topic='Fixture',title='Invented speech.',rationale='fixture',image_prompt='fixture')
  def request(path,body=None):return {'tokens':[1]} if path=='/tokenize' else {'content':json.dumps(c)}
  with patch.object(studio_ai,'_start'),patch.object(studio_ai,'shutdown'),patch.object(studio_ai,'_request',side_effect=request):
   with self.assertRaisesRegex(ValueError,'unsupported'):thumb.plan(self.words)
 def test_missing_models_fail_before_loading_video(self):
  host=FastAPI();thumb.register(host)
  with patch.object(thumb.transcription,'status',return_value={'ready':False}),patch.object(thumb,'load') as load:
   result=TestClient(host).post('/thumbnail-ai/jobs',json={'source_id':'a'*32})
  self.assertEqual(result.status_code,503);load.assert_not_called()
 def test_failed_paid_job_cannot_be_resubmitted(self):
  host=FastAPI();thumb.register(host);jid='a'*32
  job=dict(id=jid,status='completed',generation_status='failed',generated=[])
  class Provider:
   def status(self):return dict(available=True,name='fixture')
  with patch.object(thumb,'JOBS',{jid:job}),patch.object(thumb,'provider',Provider()),patch.object(thumb.IMAGE_POOL,'submit') as submit:
   result=TestClient(host).post('/thumbnail-ai/jobs/'+jid+'/generate')
  self.assertEqual(result.status_code,409);submit.assert_not_called()
 def test_cached_results_remain_downloadable_when_provider_disabled(self):
  host=FastAPI();thumb.register(host);jid='b'*32
  job=dict(id=jid,status='completed',generation_status='completed',generated=[dict(id=0,url='fixture')])
  with patch.object(thumb,'JOBS',{jid:job}),patch.object(thumb,'provider',thumb.UnavailableProvider()):
   result=TestClient(host).post('/thumbnail-ai/jobs/'+jid+'/generate')
  self.assertEqual(result.status_code,200);self.assertEqual(len(result.json()['images']),1)
 def test_cgroup_limit_uses_container_not_host(self):
  def read(path,*args,**kwargs):
   return {'/sys/fs/cgroup/memory.max':'2147483648','/sys/fs/cgroup/memory.current':'536870912'}.get(str(path).replace('\\','/'),'')
  with patch.object(Path,'read_text',read):self.assertEqual(studio_memory.container_limit(),(2147483648,1610612736))
 def test_inactive_file_cache_is_reclaimable_without_changing_limits(self):
  def read(path,*args,**kwargs):
   return {'memory.max':'2147483648','memory.current':'1610612736','memory.stat':'inactive_file 1073741824\nactive_anon 536870912'}[path.name]
  with patch.object(Path,'read_text',read):self.assertEqual(studio_memory.container_limit(),(2147483648,1610612736))
 def test_missing_vision_does_not_download_or_fabricate(self):
  with patch.object(thumbnail_vision,'status',return_value={'ready':False}),patch.object(thumbnail_vision,'request') as request:
   self.assertEqual(thumbnail_vision.describe([]),[])
  request.assert_not_called()
 def test_isolated_speech_environment_does_not_require_global_package_change(self):
  with patch.object(thumb,'SPEECH_PYTHON',Path('/fixture/python')),patch.object(thumb,'external_speech_library',return_value=True),patch.object(thumb.transcription,'status',return_value=dict(ready=False,model_installed=True,library_installed=False)):
   self.assertTrue(thumb.speech_status()['ready'])
 def test_restart_does_not_resume_or_resubmit_paid_job(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);jid='c'*32
   (root/(jid+'.json')).write_text(json.dumps(dict(status='completed',generation_status='running',generated=[dict(id=0,url='fixture')])))
   with patch.object(thumb,'ROOT',root),patch.object(thumb,'JOBS',{}):job=thumb.get_job(jid)
   self.assertEqual(job['generation_status'],'failed');self.assertEqual(len(job['generated']),1)
 def test_silent_video_requires_actual_visual_observations(self):
  with self.assertRaisesRegex(ValueError,'No usable dialogue or visual'):thumb.plan([],[])
  def request(path,body=None):return {'tokens':[1]} if path=='/tokenize' else {'content':json.dumps(dict(topic='Red flower',title='Red in focus',rationale='Fixture',image_prompt='Fixture'))}
  with patch.object(studio_ai,'_start'),patch.object(studio_ai,'shutdown'),patch.object(studio_ai,'_request',side_effect=request):
   result=thumb.plan([],[dict(time=1.5,description='A red flower among green leaves')])
  self.assertEqual(len(result['concepts']),3)
  self.assertTrue(all(c['evidence_kind']=='visual' and c['time']==1.5 for c in result['concepts']))

if __name__=='__main__':unittest.main()
