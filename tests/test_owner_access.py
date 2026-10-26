"""Owner development access never changes customer plans or provider budgets."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from fastapi import HTTPException
from owner_access import is_owner,development_access
from clipforge_studios.access import Access
from clipforge_studios.backend import kie_account

OWNER='00000000-0000-4000-8000-000000000001'
OTHER='00000000-0000-4000-8000-000000000002'
class OwnerTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{'CLIPFORGE_OWNER_USER_ID':OWNER});self.env.start();self.addCleanup(self.env.stop)
  self.owner=dict(id=OWNER,email='owner@example.invalid',email_verified=True)
 def test_only_exact_verified_uuid_gets_development_access(self):
  self.assertTrue(is_owner(self.owner));self.assertFalse(development_access(self.owner)['payment_verified'])
  for actor in [None,{},dict(self.owner,id=OTHER),dict(self.owner,email_verified=False),dict(self.owner,id='invalid')]:self.assertFalse(is_owner(actor));self.assertIsNone(development_access(actor))
  with patch.dict(os.environ,{'CLIPFORGE_OWNER_USER_ID':''}):self.assertFalse(is_owner(self.owner))
 def test_owner_can_render_without_touching_customer_billing_or_usage(self):
  with tempfile.TemporaryDirectory() as folder:
   billing=Mock(side_effect=AssertionError('Owner must not change billing'));record=Mock()
   access=Access(Path(folder)/'test.db',access=billing,record=record)
   for _ in range(6):access.finish(access.reserve(self.owner),True)
   billing.assert_not_called();record.assert_not_called()
   with access.db() as db:self.assertEqual(db.execute('select count(*) from usage').fetchone()[0],0)
 def test_regular_customer_limit_and_owner_resource_isolation_remain(self):
  with tempfile.TemporaryDirectory() as folder:
   billing=Mock(return_value=dict(allowed=False,plan='trial',reason='daily_limit'))
   access=Access(Path(folder)/'test.db',access=billing)
   with self.assertRaises(HTTPException):access.reserve(dict(self.owner,id=OTHER))
   billing.assert_called_once();access.claim('entity:fixture',OTHER)
   self.assertFalse(access.owns('entity:fixture',OWNER))
 def test_owner_entitlement_does_not_change_kie_limit_or_allowlist(self):
  with patch.dict(os.environ,{'CLIPFORGE_KIE_SPENDING_LIMIT_USD':'1','CLIPFORGE_KIE_ALLOWED_USER_IDS':OTHER}):
   token=kie_account.legacy.actor.set(self.owner)
   try:
    self.assertEqual(kie_account.limit(),200)
    with self.assertRaises(HTTPException):kie_account.user()
    with self.assertRaises(HTTPException):kie_account.reserve(None,'video',{},1000,False)
   finally:kie_account.legacy.actor.reset(token)
 def test_classic_owner_bypasses_only_trial_gate_without_usage_or_stripe_writes(self):
  from tests.test_verified_billing import IdentityTests
  with tempfile.TemporaryDirectory() as folder:
   client,scope=IdentityTests.manual_client(self,folder,dict(allowed=False,plan='trial',reason='daily_limit'))
   scope['billing_actor'].return_value=self.owner
   response=client.post('/clip_multi',data={'sections':'[{"start":"0","end":"1"}]'},files={'file':('fixture.mp4',b'contract fixture')})
   self.assertEqual(response.status_code,200,response.text)
   scope['check_clip_access'].assert_not_called();scope['record_clip_used'].assert_not_called()
