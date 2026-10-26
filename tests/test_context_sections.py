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
