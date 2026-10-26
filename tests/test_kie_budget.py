"""No live network: account-only KIE spending and durable one-shot checks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from clipforge_studios.backend import kie_budget as budget
from clipforge_studios.backend import kie_provider as kie

USER='00000000-0000-4000-8000-000000000001'
OTHER='00000000-0000-4000-8000-000000000002'
INPUT=dict(prompt='Test',first_frame_url='https://tempfile.aiquickdraw.com/ref.png',duration=5,resolution='720p',prompt_extend=False)

class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        temp=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(budget,'LEDGER',Path(temp)/'ledger.sqlite3'))
        self.stack.enter_context(patch.dict(os.environ,{
            'CLIPFORGE_KIE_TEST_USER_ID':USER,'CLIPFORGE_KIE_TEST_APPROVAL_ID':'one-approved-test',
            'CLIPFORGE_KIE_TEST_BUDGET_USD':'1','CLIPFORGE_KIE_ALLOW_PAID_REQUESTS':'0'}))
        self.stack.enter_context(patch('requests.sessions.Session.request',side_effect=AssertionError('LIVE HTTP FORBIDDEN')))
        token=budget.actor.set(dict(id=USER,email='fixture@example.invalid'))
        self.addCleanup(budget.actor.reset,token)
        self.client=kie.KieClient('contract-test')

    def test_scoped_capabilities_only_allowed_account_and_video(self):
        self.assertTrue(kie.KieVideoProvider(self.client).status()['available'])
        self.assertEqual(kie.KieVideoProvider(self.client).status()['durations'],[5])
        self.assertFalse(kie.KieImageProvider(self.client).status()['available'])
        with self.assertRaises(kie.KieError):kie.KieImageProvider(self.client).generate({},[])
        for current in (None,dict(id=OTHER)):
            token=budget.actor.set(current)
            try:
                self.assertFalse(self.client.available())
                with self.assertRaises(budget.BudgetError):
                    with budget.attempt(SimpleNamespace(duration=5)):pass
            finally:budget.actor.reset(token)

    def test_wrong_duration_invalid_budget_and_missing_ticket_fail_closed(self):
        with self.assertRaises(budget.BudgetError):
            with budget.attempt(SimpleNamespace(duration=6)):pass
        with self.assertRaises(budget.BudgetError):self.client.require_approval()
        for value in ('0.39','1.01','NaN','Infinity','invalid'):
            with patch.dict(os.environ,{'CLIPFORGE_KIE_TEST_BUDGET_USD':value}):
                self.assertFalse(self.client.available())
                with self.assertRaises(budget.BudgetError):
                    with budget.attempt(SimpleNamespace(duration=5)):pass

    def test_reservation_survives_failure_and_new_client(self):
        with self.assertRaises(RuntimeError):
            with budget.attempt(SimpleNamespace(duration=5)):raise RuntimeError('fixture upload failure')
        self.assertFalse(kie.KieClient('contract-test').available())
        with self.assertRaises(budget.BudgetError):
            with budget.attempt(SimpleNamespace(duration=5)):pass
        with budget.database() as db:
            self.assertEqual(db.execute('SELECT state,reserved_cents,credits FROM attempts').fetchall(),[('reserved',40,80)])

    def test_no_second_paid_submission_even_after_connection_error(self):
        class Transport:
            calls=0
            def request(self,*args,**kwargs):self.calls+=1;raise RuntimeError('fixture transport failure')
        transport=Transport();client=kie.KieClient('contract-test',session=transport)
        with budget.attempt(SimpleNamespace(duration=5)):
            with self.assertRaises(kie.KieError):client.task(budget.MODEL,INPUT)
            with self.assertRaises(budget.BudgetError):client.task(budget.MODEL,INPUT)
        self.assertEqual(transport.calls,1)

    def test_unapproved_model_options_and_duration_never_submitted(self):
        with budget.attempt(SimpleNamespace(duration=5)):
            for model,inputs in [('google/nano-banana-edit',INPUT),(budget.MODEL,{**INPUT,'duration':15}),
                                  (budget.MODEL,{**INPUT,'resolution':'1080p'}),(budget.MODEL,{**INPUT,'audio_url':'x'})]:
                with self.assertRaises(budget.BudgetError):budget.claim_submission(model,inputs)
            budget.claim_submission(budget.MODEL,INPUT)
            budget.record_task('fixture-task')
            self.client.require_approval()  # Poll/download remain possible after reservation.
            with self.assertRaises(budget.BudgetError):budget.claim_submission(budget.MODEL,INPUT)

    def test_concurrent_attempts_have_one_winner(self):
        # Initialize before concurrent first use; each worker uses its own connection.
        with budget.database():pass
        def run(_):
            token=budget.actor.set(dict(id=USER))
            try:
                with budget.attempt(SimpleNamespace(duration=5)):budget.claim_submission(budget.MODEL,INPUT)
                return 1
            except budget.BudgetError:return 0
            finally:budget.actor.reset(token)
        with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(run,range(12))),1)

    def test_verified_actor_propagates_to_worker_without_auth_changes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from clipforge_studios.access import Access, StudioAccess, BillingPool
        import uuid
        with tempfile.TemporaryDirectory() as temp, ThreadPoolExecutor(max_workers=1) as executor:
            manager=Access(Path(temp)/'owners.sqlite3',
                authenticate=lambda token:dict(id=USER if token=='approved' else OTHER,email='fixture@example.invalid'),
                access=lambda email:dict(allowed=True,plan='active'),record=lambda email:None)
            pool=BillingPool(executor);app=FastAPI();futures=[]
            @app.get('/image-video/capabilities')
            def capabilities():return kie.KieVideoProvider(self.client).status()
            @app.post('/image-video/jobs',status_code=202)
            def generate():
                job=dict(id=uuid.uuid4().hex,status='queued')
                def work(job):
                    with budget.attempt(SimpleNamespace(duration=5)):budget.claim_submission(budget.MODEL,INPUT)
                    job['status']='completed'
                futures.append(pool.submit(work,job));return job
            app.add_middleware(StudioAccess,access=manager)
            http=TestClient(app)
            self.assertEqual(http.get('/image-video/capabilities').status_code,401)
            for token,enabled in [('other',False),('approved',True)]:
                self.assertEqual(http.get('/image-video/capabilities',headers={'Authorization':'Bearer '+token}).json()['available'],enabled)
            self.assertEqual(http.post('/image-video/jobs',headers={'Authorization':'Bearer approved'}).status_code,202)
            futures[0].result(timeout=5)
            self.assertFalse(http.get('/image-video/capabilities',headers={'Authorization':'Bearer approved'}).json()['available'])

if __name__=='__main__':unittest.main()
