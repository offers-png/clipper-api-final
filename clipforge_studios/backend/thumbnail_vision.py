"""Optional offline SmolVLM frame descriptions; never downloads models or uses KIE."""
import base64
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
from . import studio_ai, studio_memory
from .transcription import release_memory
from .resource_paths import MODELS, DATA
from .inference_lock import lock

MODEL=Path(os.getenv('CLIPFORGE_VISION_MODEL') or MODELS/'vision/SmolVLM-256M-Instruct-Q8_0.gguf').resolve()
PROJECTOR=Path(os.getenv('CLIPFORGE_VISION_PROJECTOR') or MODELS/'vision/mmproj-SmolVLM-256M-Instruct-f16.gguf').resolve()

def status():
    return dict(ready=MODEL.is_file() and PROJECTOR.is_file() and studio_ai.RUNTIME.is_file(),
                model=MODEL.stem,download_automatic=False,
                method='AI descriptions of sampled actual frames; not full-video observation or identity recognition')

def request(path,body=None,timeout=90):
    req=urllib.request.Request('http://127.0.0.1:8003'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)

def describe(frames,on_progress=None):
    if not status()['ready']:return []
    with lock,studio_ai._lock:
        studio_ai.shutdown();release_memory()
        studio_memory.release_model_cache(MODEL,PROJECTOR)
        free=studio_memory.available()
        required=max(1024**3,MODEL.stat().st_size+PROJECTOR.stat().st_size+512*1024**2)
        if free is not None and free<required:raise RuntimeError('Insufficient RAM for local visual analysis; stopped before loading')
        stop=threading.Event();errors=[]
        with (DATA/'thumbnail-vision.log').open('ab') as log:
            process=subprocess.Popen([str(studio_ai.RUNTIME),'-m',str(MODEL),'--mmproj',str(PROJECTOR),
                '--host','127.0.0.1','--port','8003','-c','2048','-t','2','-b','128','-ub','32',
                '-ngl','0','--parallel','1','--no-warmup','--no-webui'],stdout=log,stderr=log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def watch():
            while not stop.wait(.25) and process.poll() is None:
                available=studio_memory.available()
                if available is not None and available<384*1024**2:
                    errors.append('Visual analysis stopped safely: available RAM fell below 384 MiB')
                    process.terminate();return
        threading.Thread(target=watch,daemon=True).start()
        try:
            for _ in range(90):
                if errors:raise RuntimeError(errors[0])
                if process.poll() is not None:raise RuntimeError('Local vision runtime could not start; no cloud fallback was used')
                try:
                    if request('/health',timeout=1).get('status')=='ok':break
                except Exception:pass
                time.sleep(1)
            else:raise RuntimeError('Local vision startup timed out')
            observations=[]
            for i,frame in enumerate(frames):
                raw=Path(frame['path']).read_bytes()
                if len(raw)>5_000_000:raise ValueError('Visual reference exceeds size limit')
                message=[{'type':'text','text':'Describe the main visible non-text subject and background in one short sentence. Is the subject a real person, cartoon, animal or object? Ignore all captions, written words and labels. Do not read text or turn words into physical objects. Do not identify people, infer emotions or invent unseen events.'},
                         {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(raw).decode()}}]
                response=request('/v1/chat/completions',dict(messages=[dict(role='user',content=message)],
                    max_tokens=90,temperature=0),timeout=120)
                text=response['choices'][0]['message']['content']
                if not isinstance(text,str) or not text.strip():raise ValueError('Vision model returned no description')
                observations.append(dict(time=frame['time'],description=text.strip()[:600]))
                if on_progress:on_progress(i+1,len(frames))
            if errors:raise RuntimeError(errors[0])
            return observations
        finally:
            stop.set()
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            studio_memory.release_model_cache(MODEL,PROJECTOR)
