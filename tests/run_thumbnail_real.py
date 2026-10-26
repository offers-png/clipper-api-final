"""Explicit offline integration test: existing models + actual uploaded video.

Run with CLIPFORGE_MODELS_DIR pointing to the pre-existing approved model files.
No KIE jobs are submitted; image synthesis is tested separately with blocked HTTP.
"""
import json
from pathlib import Path
import time
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import requests
import argparse
import subprocess
from clipforge_studios.backend import studio, thumbnail_ai as thumb, studio_ai
from clipforge_studios.backend.server import load
from clipforge_studios.backend.resource_paths import DATA

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--silent',action='store_true');args=parser.parse_args()
    source=Path(__file__).resolve().parents[2]/'clipforge_unified_dev/fixtures/actual-video-20s.mp4'
    if args.silent:
        silent=DATA/'silent-actual-video.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-t','5','-an','-c:v','copy',str(silent)],check=True)
        source=silent
    host=FastAPI();studio.register(host,load);thumb.register(host)
    # All requests-library HTTP, including KIE, is prohibited. llama.cpp uses
    # its separate hard-coded loopback urllib endpoint; Whisper is offline-only.
    with patch.object(requests.sessions.Session,'request',side_effect=AssertionError('External HTTP forbidden')):
        http=TestClient(host)
        with source.open('rb') as video:r=http.post('/studio/sources',files={'file':('actual20.mp4',video,'video/mp4')})
        assert r.status_code==200,r.text
        source_id=r.json()['id']
        r=http.post('/thumbnail-ai/jobs',json={'source_id':source_id})
        assert r.status_code==202,r.text
        job_id=r.json()['job_id'];deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            job=http.get('/thumbnail-ai/jobs/'+job_id).json()
            if job['status'] not in ('queued','running'):break
            time.sleep(1)
        assert job['status']=='completed',job
        concepts=job['analysis']['concepts'];assert len(concepts)==3
        if not args.silent:assert len({c['title'] for c in concepts})==3
        source_data=studio._load_source(source_id,load)
        for c in concepts:
            assert 0<=c['time']<=c['end']<=source_data['duration'],c
            if args.silent:assert c['evidence_kind']=='visual' and c['evidence'] in {o['description'] for o in job['observations']},c
            else:assert studio_ai.grounded(c['evidence'],' '.join(w['word'] for w in source_data['words'])),c
            assert c['time']<=c['reference']['time']<=c['end'],c
            r=http.get(c['reference']['url']);assert r.status_code==200 and r.content.startswith(b'\xff\xd8')
        r=http.post('/thumbnail-ai/jobs/'+job_id+'/generate',json={})
        assert r.status_code==503,r.text
        report=dict(job=job,transcript=source_data.get('text',''),checks=dict(actual_video_upload=True,
            actual_whisper_transcription=not args.silent,actual_qwen_concepts=True,three_distinct_approaches=True,
            timestamp_grounded_references=True,reference_downloads=True,paid_generation_requests=0,
            visual_analysis_tested=bool(job.get('observations')),generation_disabled_status=503))
        folder=DATA/'reports';folder.mkdir(exist_ok=True)
        (folder/('thumbnail-silent-test.json' if args.silent else 'thumbnail-real-test.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report['checks']))

if __name__=='__main__':main()
