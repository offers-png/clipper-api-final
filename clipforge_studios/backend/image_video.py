"""Image-to-video provider boundary. No animation fallback, API lookup or downloads."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import re
from typing import Literal, Protocol
import uuid
import threading
from fastapi import HTTPException
from pydantic import BaseModel, Field
from . import editor

class Generation(BaseModel):
    image_id:str=Field(pattern='^[a-f0-9]{32}$')
    prompt:str=Field(default='',max_length=2000)
    duration:int=Field(default=5,ge=2,le=30)
    ratio:Literal['16:9','9:16','1:1']='16:9'
    motion:Literal['Cinematic','Natural movement','Camera zoom','Camera pan','Animated character','Custom prompt']='Cinematic'

class VideoProvider(Protocol):
    """Approved adapters synthesize actual video bytes from the image and request.

    Implement status() and generate(image_path, request). No production adapter is
    registered locally. Camera-style choices describe requested AI motion, never
    an FFmpeg substitute. Adapter capabilities must declare duration/ratio limits.
    """
    def status(self)->dict: ...
    def generate(self,image_path:Path,request:Generation)->bytes: ...

class Unavailable:
    def status(self):return dict(available=False,name=None,reason='Awaiting connection to a compatible image-to-video provider. No local video-generation model is installed; no basic animation is substituted.',durations=[],ratios=[])
    def generate(self,image_path,request):raise RuntimeError(self.status()['reason'])

from .video_provider_config import load as load_provider
provider:VideoProvider=load_provider(Unavailable())
POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='image-video-provider')
STATE={}
STATE_LOCK=threading.Lock()

def worker(job,body):
    folder=editor.JOBS/job['id'];folder.mkdir(exist_ok=True)
    try:
        job.update(status='running',stage='Generating with connected video provider')
        try:raw=provider.generate(Path(editor.asset(body.image_id)['path']),body)
        except Exception:raise RuntimeError('Video provider request failed. It may still finish remotely; check the provider dashboard before retrying. No automatic paid retry or replacement result was attempted.') from None
        if not isinstance(raw,bytes) or len(raw)>512*1024**2:raise ValueError('Provider returned invalid or oversized video')
        p=folder/'provider-video.input';p.write_bytes(raw)
        info=editor.probe(p)
        if info['kind']!='video' or abs(info['duration']-body.duration)>.5:raise ValueError('Provider output duration does not match the request')
        w,h=editor.SIZES[body.ratio]
        if abs(info['width']/info['height']-w/h)>.04:raise ValueError('Provider output aspect ratio does not match the request')
        # Normalize actual synthesized output to MP4 for the shared editor.
        editor.run(['-i',str(p.resolve()),'-map','0:v:0','-map','0:a?','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','finished.mp4'],folder)
        a=editor.save_asset(folder/'finished.mp4','AI generated video')
        job.update(status='completed',stage='Ready',asset=a,url=f'/editor/files/{job["id"]}/finished.mp4',provider=provider.status()['name'])
    except Exception as e:job.update(status='failed',stage='Stopped',error=str(e))
    finally:
        (folder/'provider-video.input').unlink(missing_ok=True)
        (folder/'image-video.json').write_text(json.dumps(job),encoding='utf-8')

def register(app):
    @app.get('/image-video/capabilities')
    def capabilities():return provider.status()

    @app.post('/image-video/jobs',status_code=202)
    def generate(body:Generation):
        try:image=editor.asset(body.image_id)
        except Exception:raise HTTPException(404,'Uploaded image not found')
        if image['kind']!='image':raise HTTPException(422,'Image-to-video requires a still image')
        if body.motion=='Custom prompt' and not body.prompt.strip():raise HTTPException(422,'Enter a custom motion prompt')
        caps=provider.status()
        if not caps['available']:raise HTTPException(503,caps['reason'])
        if body.duration not in caps['durations'] or body.ratio not in caps['ratios']:raise HTTPException(422,'Connected provider does not support this duration/aspect ratio')
        with STATE_LOCK:
            if any(j['status'] in ('queued','running') for j in STATE.values()):raise HTTPException(409,'A video-generation request is already running')
            job=dict(id=uuid.uuid4().hex,status='queued',stage='Queued')
            STATE[job['id']]=job;POOL.submit(worker,job,body)
        return job

    @app.get('/image-video/jobs/{job_id}')
    def status(job_id:str):
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise HTTPException(404)
        if job_id in STATE:return dict(STATE[job_id])
        p=editor.JOBS/job_id/'image-video.json'
        if not p.is_file():raise HTTPException(404)
        return json.loads(p.read_text(encoding='utf-8'))
