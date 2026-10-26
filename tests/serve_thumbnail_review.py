"""Loopback-only UI contract fixture; no keys, real auth, KIE calls or AI claim."""
import json
from pathlib import Path
import uuid
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from clipforge_studios.install import install
from clipforge_studios.backend import thumbnail_ai as thumb, studio, media

app=FastAPI()
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5174'],allow_headers=['*'],allow_methods=['*'])
install(app,local_only=True)

@app.post('/__test/thumbnail-fixture')
def fixture():
    jid=uuid.uuid4().hex;folder=thumb.ROOT/jid;folder.mkdir()
    source=Path(__file__).resolve().parents[2]/'clipforge_unified_dev/fixtures/actual-video-20s.mp4'
    concepts=[]
    for i in range(3):
        t=i*5+1;path=folder/f'reference_{i}.jpg'
        code,_=media.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(source),'-frames:v','1','-vf','scale=640:-2',str(path)])
        if code:raise RuntimeError('Could not create actual reference fixture')
        concepts.append(dict(id=i,title=f'UI fixture {i+1}',evidence='Explicit UI fixture',time=t,end=t+1,
            rationale='UI contract fixture, not AI analysis.',image_prompt='Not submitted to any image provider.',
            approach=['Editorial reveal','Curiosity / question','Character illustration concept'][i],
            reference=dict(time=t,url=f'/thumbnail-ai/jobs/{jid}/reference/{i}')))
    job=dict(id=jid,status='completed',stage='UI fixture only',analysis=dict(topic='Explicit UI contract fixture',concepts=concepts),
        observations=[],subject_basis='UI fixture only',visual_analysis='No AI analysis or synthesis in this UI fixture.',scene_changes=[],warnings=[])
    thumb.JOBS[jid]=job;(thumb.ROOT/(jid+'.json')).write_text(json.dumps(job),encoding='utf-8')
    return dict(job_id=jid)
