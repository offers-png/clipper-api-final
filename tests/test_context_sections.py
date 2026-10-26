import json
import unittest
from unittest.mock import patch
from clipforge_studios.backend import studio_ai as ai

class ContextTests(unittest.TestCase):
 def tokenizer(self,path,body=None,**kwargs):return dict(tokens=list(body['content']))
 def test_lossless_ordered_splits_including_unicode_and_long_words(self):
  for text in ['Actual dialogue. '*400,'word '*1200,'\u6559\u80b2'*1500]:
   with patch.object(ai,'_request',side_effect=self.tokenizer):parts=ai.context_sections(text,80)
   self.assertEqual(''.join(parts),text);self.assertGreater(len(parts),1)
   self.assertTrue(all(len(json.dumps(p,ensure_ascii=False))<=80 for p in parts))
 def test_long_candidate_analyzes_every_section_and_returns_one_candidate(self):
  text='A real source sentence explains a useful idea. '*100;seen=[]
  def request(path,body=None,**kwargs):
   if path=='/tokenize':return dict(tokens=list(body['content']))
   prompt=body['prompt'];raw=prompt.split('<|im_start|>user\n')[1].split('<|im_end|>')[0];item=json.loads(raw)[0];seen.append(item['transcript'])
   quote=item['allowed_quotes'][0]
   return dict(content=json.dumps(dict(moments=[dict(id=item['id'],suitable=True,score=.8,evidence=quote,hook_quote=quote,context_quote=quote,reason='Explicit contract fixture',emoji='')])))
  with patch.object(ai,'_start'),patch.object(ai,'shutdown'),patch.object(ai,'_request',side_effect=request):result=ai.analyse([dict(id=17,text=text)],'educational')
  self.assertEqual(''.join(seen),text);self.assertEqual(len(result),1);self.assertEqual(result[0]['id'],17)
 def test_editing_assistant_uses_all_sections_without_overflow(self):
  from clipforge_studios.backend import legacy
  text='Actual source dialogue about useful editing. '*100;seen=[]
  def request(path,body=None,**kwargs):
   if path=='/tokenize':return dict(tokens=list(body['content']))
   raw=body['prompt'].split('<|im_start|>user\n')[1].split('<|im_end|>')[0]
   seen.append(json.loads(raw)['transcript']);self.assertLessEqual(len(body['prompt']),1250)
   return dict(content='Explicit local transport fixture')
  with patch.object(ai,'_start'),patch.object(ai,'shutdown'),patch.object(ai,'_request',side_effect=request):result=legacy.answer(legacy.Question(transcript=text,prompt='Summarize'))
  self.assertEqual(''.join(seen),text);self.assertEqual(result['sections_analyzed'],len(seen));self.assertGreater(len(seen),1)
 def test_long_assistant_job_is_authenticated_and_owned(self):
  import tempfile
  from pathlib import Path
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  from clipforge_studios.backend import legacy
  from clipforge_studios.access import Access,StudioAccess
  class Immediate:
   def submit(self,fn,*args):fn(*args)
  with tempfile.TemporaryDirectory() as folder:
   access=Access(Path(folder)/'access.db',authenticate=lambda token:dict(id=token,email='fixture@example.invalid'))
   app=FastAPI();legacy.register(app);host=FastAPI();host.mount('/studios-api',StudioAccess(app,access));client=TestClient(host)
   with patch.object(legacy,'ASSISTANT_JOBS',Path(folder)),patch.object(legacy.studio,'_pool',Immediate()),patch.object(legacy.studio_ai,'status',return_value=dict(ready=True)),patch.object(legacy,'answer',return_value=dict(reply='Explicit contract fixture',sections_analyzed=2,engine='fixture')):
    r=client.post('/studios-api/local-original/assistant-jobs',headers={'Authorization':'Bearer owner-fixture'},json=dict(transcript='actual source '*1400,prompt='Summarize'))
    self.assertEqual(r.status_code,202,r.text);jid=r.json()['id']
    self.assertEqual(client.get('/studios-api/local-original/assistant-jobs/'+jid,headers={'Authorization':'Bearer owner-fixture'}).status_code,200)
    self.assertEqual(client.get('/studios-api/local-original/assistant-jobs/'+jid,headers={'Authorization':'Bearer other-fixture'}).status_code,404)
    self.assertEqual(client.get('/studios-api/local-original/assistant-jobs/'+jid).status_code,401)
