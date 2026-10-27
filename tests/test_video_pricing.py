"""Offline pricing/schema/security contracts. Live HTTP is forbidden by fixtures."""
from decimal import Decimal
import json
import unittest
from clipforge_studios.backend import video_models as models,kie_account as policy,kie_provider as kie
from . import test_kie_account as fixtures

class PricingTests(unittest.TestCase):
 setUpClass=classmethod(fixtures.AccountTests.setUpClass.__func__)
 tearDownClass=classmethod(fixtures.AccountTests.tearDownClass.__func__)
 setUp=fixtures.AccountTests.setUp
 def test_existing_factory_loads_expanded_catalog_instead_of_falling_back(self):
  from unittest.mock import patch
  from clipforge_studios.backend.video_provider_config import load
  with patch.dict('os.environ',{'CLIPFORGE_VIDEO_PROVIDER_API_KEY':'contract-test','CLIPFORGE_VIDEO_PROVIDER_FACTORY':''}):
   provider=load(None)
   self.assertTrue(provider.status()['available']);self.assertEqual(len(provider.status()['models']),74)
 def test_every_reviewed_variant_has_exact_cost_and_schema_payload(self):
  self.assertGreaterEqual(len(models.MODELS),74)
  for m in models.MODELS.values():
   for p in m['prices']:
    with self.subTest(model=m['id'],price=p):
     b=dict(model=m['id'],mode=m['mode'],duration=p['duration'],resolution=p['resolution'],audio=p['audio'],ratio='16:9',prompt='Actual source reference.',motion='Custom prompt')
     estimate=models.estimate(b)
     self.assertEqual(Decimal(str(estimate['cost_credits'])),Decimal(p['credits']))
     self.assertEqual(Decimal(estimate['cost_usd']),Decimal(p['credits'])*Decimal('.005'))
     payload=models.payload(b,b['prompt'],'https://cdn.aiquickdraw.com/reference.png' if m['mode']=='image' else None)
     for field in m['schema_required']:self.assertIn(field,payload)
     for key,value in payload.items():
      if key in ('enable_prompt_expansion','prompt_extend'):continue
      self.assertIn(key,m['schema_fields'])
      schema=m['schema_fields'][key]
      if 'enum' in schema:self.assertIn(value,schema['enum'])
      if schema.get('type')=='string':self.assertIsInstance(value,str)
      elif schema.get('type')=='integer':self.assertEqual(type(value),int)
      elif schema.get('type')=='boolean':self.assertEqual(type(value),bool)
     if m['provider_model']=='veo-3-1':self.assertIs(payload['enable_fallback'],False)
 def test_fractional_reservations_preserve_cap_and_history(self):
  b=dict(model='grok-imagine/text-to-video',mode='text',duration=7,resolution='720p',audio='model',ratio='16:9',prompt='A cat walks.',motion='Custom prompt')
  # 31.5 credits = $0.15750. Prior verified $0.40 must stay counted.
  q=policy.quote('video',b,1000);self.assertEqual(q['cost_credits'],31.5)
  self.assertEqual(Decimal(q['remaining_after_usd']),Decimal('.4425'))
  for _ in range(3):
   q=policy.quote('video',b,1000);qid=policy.reserve(q['quote_id'],'video',b,1000,True)
   with policy.execution(qid):policy.claim_submission('grok-imagine/text-to-video',models.payload(b,b['prompt']),1000);policy.terminal('success')
   policy.finish(qid,True)
  self.assertEqual(Decimal(policy.status(True)['committed_usd']),Decimal('.8725'))
  q=policy.quote('video',b,1000);self.assertFalse(q['can_submit'])
  with self.assertRaises(Exception):policy.reserve(q['quote_id'],'video',b,1000,True)
 def test_resolution_audio_prompt_and_model_cannot_be_swapped(self):
  b=dict(model='pixverse-v6/text-to-video',mode='text',duration=5,resolution='720p',audio='off',ratio='9:16',prompt='Actual scene.',motion='Custom prompt')
  q=policy.quote('video',b,1000);qid=policy.reserve(q['quote_id'],'video',b,1000,True)
  with policy.execution(qid):
   original=models.payload(b,b['prompt'])
   for key,value in [('quality','1080p'),('generate_audio_switch',True),('duration',10),('prompt','Unquoted prompt'),('aspect_ratio','16:9')]:
    with self.assertRaises(RuntimeError):policy.claim_submission('pixverse-v6/text-to-video',{**original,key:value},1000)
   with self.assertRaises(RuntimeError):policy.claim_submission('pixverse-v6-max/text-to-video',original,1000)
 def test_unpriced_combinations_fail_closed(self):
  for b in [dict(model='runway::text',mode='text',duration=10,resolution='1080p'),dict(model='grok-imagine-video-1-5-preview',mode='image',duration=5,resolution='1080p'),dict(model='wan/2-5-text-to-video',mode='text',duration=5,resolution='720p',audio='on')]:
   with self.assertRaises(Exception):models.estimate(b)
 def test_old_ledger_migration_keeps_fractional_and_integer_exposure(self):
  with policy.database() as db:
   columns={r[1] for r in db.execute('PRAGMA table_info(quotes)')}
   self.assertTrue({'credits_units','held_units','spec'}<=columns)
  self.assertEqual(Decimal(policy.status(True)['committed_usd']),Decimal('.4'))
 def test_veo_returns_requested_high_resolution_url_not_origin(self):
  class Transport(fixtures.CreditsTransport):
   def request(self,method,url,**kw):
    if url.endswith('/recordInfo'):
     return fixtures.Response(dict(code=200,data=dict(state='success',resultJson=json.dumps(dict(data=dict(result_urls=['https://cdn.aiquickdraw.com/4k.mp4'],origin_urls=['https://cdn.aiquickdraw.com/720.mp4']))))))
    return super().request(method,url,**kw)
  # Contract transport only; execute under a valid quoted reservation.
  b=dict(model='veo-3-1::text::lite',mode='text',duration=4,resolution='4k',audio='model',ratio='16:9',prompt='A cat walks.',motion='Custom prompt')
  # The real $1 test account would correctly block $0.75 after historical spend.
  from unittest.mock import patch
  with patch.dict('os.environ',{'CLIPFORGE_KIE_SPENDING_LIMIT_USD':'2'}):
   q=policy.quote('video',b,1000);qid=policy.reserve(q['quote_id'],'video',b,1000,True)
   client=kie.KieClient('contract-test',session=Transport(self.image,states=['success']),sleep=lambda _:None)
   with policy.execution(qid):self.assertEqual(client.task('veo-3-1',models.payload(b,b['prompt'])),'https://cdn.aiquickdraw.com/4k.mp4')

if __name__=='__main__':unittest.main()
