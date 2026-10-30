"""Daily allowance for OpenAI-costly analyses (thumbnail analysis, Text-to-Clip transcription), separate from clips."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from clipforge_studios.access import Access, BillingPool, StudioAccess, analysis


class AnalysisLimitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.records, self.plan, self.allowed = [], 'trial', True

        def authenticate(token):
            if token not in ('local-test-a', 'local-test-b'):
                raise HTTPException(401, 'Invalid test token')
            return dict(id=token, email=token + '@example.invalid')

        def access(email):
            return dict(allowed=self.allowed, plan=self.plan, reason='ok', clips_remaining=3)
        self.access = Access(Path(temp.name) / 'usage.sqlite3', authenticate, access, self.records.append, limit=lambda: 3)
        app = FastAPI(); self.pool = BillingPool(ThreadPoolExecutor(max_workers=1)); self.addCleanup(self.pool.base.shutdown)
        self.fail_next = False; self.jobs = {}

        @app.post('/api/videos')
        def transcribe():
            if self.fail_next:
                raise HTTPException(422, 'No speech detected')
            return dict(video_id=uuid.uuid4().hex)

        @app.post('/thumbnail-ai/jobs', status_code=202)
        def analyse(body: dict):
            job = dict(id=uuid.uuid4().hex, status='queued'); self.jobs[job['id']] = job
            self.pool.submit(lambda j: j.update(status='completed'), job)
            return dict(job_id=job['id'])

        @app.post('/local-original/manual')
        def clip(body: dict):
            return dict(id=uuid.uuid4().hex)
        host = FastAPI(); host.mount('/studios-api', StudioAccess(app, self.access))
        self.http = TestClient(host)

    def post(self, path, who='a', **kwargs):
        return self.http.post('/studios-api' + path, headers={'Authorization': 'Bearer local-test-' + who}, **kwargs)

    def test_only_the_costly_routes_count_as_analyses(self):
        self.assertTrue(all(analysis(p) for p in ('/thumbnail-ai/jobs', '/api/videos', '/api/videos/url')))
        self.assertFalse(any(analysis(p) for p in ('/thumbnail-ai/jobs/' + 'a' * 32 + '/design', '/studio/sources', '/studio/jobs')))

    def test_free_trial_gets_three_analyses_a_day(self):
        for _ in range(3):
            self.assertEqual(self.post('/api/videos').status_code, 200)
        blocked = self.post('/api/videos')
        self.assertEqual(blocked.status_code, 403)
        self.assertIn('Daily video analysis limit reached (3 per day)', blocked.json()['detail'])
        self.assertEqual(self.post('/api/videos', who='b').status_code, 200)  # per user
        self.assertEqual(self.records, [])  # analyses never enter the clip billing ledger

    def test_failed_analyses_do_not_use_the_allowance(self):
        self.fail_next = True
        for _ in range(5):
            self.assertEqual(self.post('/api/videos').status_code, 422)
        self.fail_next = False
        self.assertEqual(self.post('/api/videos').status_code, 200)

    def test_background_thumbnail_analysis_counts_when_it_completes(self):
        for _ in range(3):
            self.assertEqual(self.post('/thumbnail-ai/jobs', json={}).status_code, 202)
            time.sleep(.05)
        self.assertEqual(self.post('/thumbnail-ai/jobs', json={}).status_code, 403)

    def test_paid_plans_get_a_larger_adjustable_allowance(self):
        self.plan = 'active'
        with patch.dict(os.environ, {'CLIPFORGE_DAILY_ANALYSES_PAID': '4'}):
            codes = [self.post('/api/videos').status_code for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 200, 403])

    def test_clip_and_analysis_allowances_are_separate(self):
        self.allowed = False  # clip quota used up for today
        self.assertEqual(self.post('/local-original/manual', json={}).status_code, 403)
        self.assertEqual(self.post('/api/videos').status_code, 200)
        self.plan = 'expired'
        self.assertEqual(self.post('/api/videos').status_code, 403)  # still needs an active plan or trial

    def test_existing_usage_database_is_upgraded_and_old_rows_stay_clips(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'old.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE usage (id TEXT PRIMARY KEY,user TEXT,day TEXT,status TEXT,created REAL)')
                db.execute("INSERT INTO usage VALUES ('old','u','2026-10-29','success',0)")
            Access(path, lambda t: None, lambda e: {}, lambda e: None)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT coalesce(kind,'clip') FROM usage WHERE id='old'").fetchone()[0], 'clip')
            Access(path, lambda t: None, lambda e: {}, lambda e: None)  # second start: no error

    def test_owner_development_has_no_limit(self):
        with patch('owner_access.is_owner', return_value=True):
            codes = [self.post('/api/videos').status_code for _ in range(6)]
        self.assertEqual(codes, [200] * 6)


if __name__ == '__main__':
    unittest.main()
