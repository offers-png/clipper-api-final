"""Additive Auto Clip Studio; existing transcript selection endpoints stay unchanged."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from zipfile import ZipFile, ZIP_STORED
from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal
from . import media, studio_ai, studio_render
from .transcription import transcribe

ROOT=Path(__file__).parent
from .resource_paths import DATA as DATA_ROOT
DATA=DATA_ROOT/'studio'
SOURCES=DATA/'sources'
JOBS=DATA/'jobs'
for path in (SOURCES,JOBS):path.mkdir(parents=True,exist_ok=True)
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='studio')
_lock=threading.Lock()
_jobs={}


class Enhancements(BaseModel):
    subtitles: bool=True
    animated: bool=False
    hooks: bool=False
    context: bool=False
    zoom: bool=False
    transitions: bool=False
    vertical: bool=True
    crop: Literal['fit','face']='fit'
    thumbnail: bool=True
    emoji: bool=False


class StudioRequest(BaseModel):
    source_id: str
    mode: Literal['full','highlights']='full'
    duration: float=Field(default=30,ge=1,le=3600,allow_inf_nan=False)
    category: Literal['best','funny','speech','educational','motivational','emotional','viral','important']='best'
    count: int=Field(default=3,ge=1,le=100)
    enhancements: Enhancements=Field(default_factory=Enhancements)


def full_ranges(duration,target):
    if not math.isfinite(duration) or duration<=0:raise ValueError('Invalid source duration')
    if math.ceil(duration/target)>1000:raise ValueError('More than 1,000 clips requested. Choose a longer duration; no footage has been discarded.')
    return [dict(start=i*target,end=min(duration,(i+1)*target)) for i in range(math.ceil(duration/target))]


def candidate_ranges(words,target):
    starts=[0]+[i+1 for i,w in enumerate(words[:-1]) if re.search(r'[.!?]["\u201d\']?$',w['word'])]
    ends=sorted(set([i for i,w in enumerate(words) if re.search(r'[.!?]["\u201d\']?$',w['word'])]+[len(words)-1]))
    result=[]
    for first in starts:
        start=words[first]['start']
        possible=[i for i in ends if i>=first and abs(words[i]['end']-start-target)<=max(1,target*.2)]
        if not possible:continue  # Highlights prefer whole sentences; never invent or stretch timing.
        last=min(possible,key=lambda i:abs(words[i]['end']-start-target))
        result.append(dict(id=len(result),first=first,last=last,start=start,end=words[last]['end'],
            text=' '.join(w['word'] for w in words[first:last+1])))
    return result


def _save(job):
    temporary=JOBS/(job['id']+'.tmp')
    temporary.write_text(json.dumps(job,ensure_ascii=False),encoding='utf-8')
    # Windows readers/antivirus can briefly hold a destination handle during polls.
    # A progress-cache write must never abort a successful FFmpeg render.
    for attempt in range(10):
        try:
            temporary.replace(JOBS/(job['id']+'.json'))
            return True
        except PermissionError:
            time.sleep(.025*(attempt+1))
    logging.warning('Studio job cache temporarily locked; in-memory progress remains available: %s',job['id'])
    return False


def update(job,**fields):
    with _lock:
        job.update(fields,updated=time.time())
        _save(job)


def _load_source(source_id,existing_loader):
    if not re.fullmatch('[a-f0-9]{32}',source_id):raise ValueError('Invalid source ID')
    path=SOURCES/(source_id+'.json')
    if path.is_file():
        data=json.loads(path.read_text(encoding='utf-8'))
        if not Path(data['source']).is_file():raise ValueError('Source file is missing')
        return data
    return existing_loader(source_id)


def _words(data,job):
    if data.get('words'):return data['words']
    update(job,stage='Transcribing actual audio locally',progress=.05)
    audio=Path(media.TMP_DIR)/(job['id']+'.wav')
    try:
        code,_=media.run(['ffmpeg','-v','error','-y','-i',data['source'],'-map','0:a:0',
            '-af','aresample=async=1:first_pts=0','-ar','16000','-ac','1','-c:a','pcm_s16le',str(audio)])
        if code:
            job['warnings'].append('No usable audio stream: no subtitles or speech-based analysis were generated.')
            return []
        result=transcribe(audio)
        data.update(result)
        # Cache only actual recognition; no synthetic text or timestamps.
        if data.get('studio_source'):
            (SOURCES/(data['id']+'.json')).write_text(json.dumps(data),encoding='utf-8')
        return result['words']
    finally:audio.unlink(missing_ok=True)


def _worker(job,body,data):
    try:
        update(job,status='running',stage='Inspecting source',progress=.02)
        opts=body.enhancements
        needs_ai=body.mode=='highlights' or opts.hooks or opts.context or opts.emoji
        if needs_ai and not studio_ai.status()['ready']:
            raise ValueError('Local content-analysis model is not installed. Turn off AI hooks/context/emoji and use Full Video, or approve/install the local model. No mock analysis is substituted.')
        words=_words(data,job) if needs_ai or opts.subtitles or opts.animated else []
        analyses={}
        if body.mode=='full':
            clips=full_ranges(data['duration'],body.duration)
            for i,c in enumerate(clips):
                c.update(id=i,text=' '.join(w['word'] for w in words if w['end']>c['start'] and w['start']<c['end']))
            if needs_ai:
                update(job,stage='Generating grounded quotes with local AI',progress=.12)
                for item in studio_ai.analyse([c for c in clips if c['text']],body.category,
                    lambda fraction:update(job,progress=.12+.06*fraction)):analyses[item['id']]=item
        else:
            if not words:raise ValueError('No speech found. This local model analyses transcript content; it cannot infer silent visual moments.')
            update(job,stage='Ranking real transcript content with local AI',progress=.12)
            options=candidate_ranges(words,body.duration)
            if not options:raise ValueError('No complete sentence ranges fit this duration. Choose another duration.')
            clips=[];seen=set()
            for item in studio_ai.analyse(options,body.category,
                lambda fraction:update(job,progress=.12+.06*fraction)):
                if not item['suitable'] or item['score']<.65 or item['id'] in seen:continue
                c=options[item['id']]
                if any(c['start']<p['end'] and c['end']>p['start'] for p in clips):continue
                clips.append(c);analyses[c['id']]=item;seen.add(c['id'])
                if len(clips)==body.count:break
            if not clips:
                update(job,status='completed',stage='No suitable moments',progress=1,
                    message=f'Local AI found no sufficiently supported {studio_ai.CATEGORIES[body.category].lower()} in the transcript. No content was invented.',items=[],coverage=None)
                return
            if len(clips)<body.count:job['warnings'].append(f'Only {len(clips)} suitable, non-overlapping moments were found for {body.count} requested clips.')
        update(job,stage='Rendering clips locally',total=len(clips),progress=.2)
        output=JOBS/job['id'];output.mkdir(exist_ok=True)
        items=[]
        for index,clip in enumerate(clips):
            folder=output/f'clip_{index+1:03d}'
            update(job,stage=f'Rendering clip {index+1} of {len(clips)}',current=index+1)
            def progress(fraction):update(job,progress=.2+.75*(index+fraction)/len(clips))
            rendered=studio_render.render(data['source'],clip,folder,opts,words,analyses.get(clip['id'],{}),progress)
            base=f'/studio/files/{job["id"]}/{folder.name}/'
            rendered.update(index=index+1,preview_url=base+'clip.mp4',download_url=base+'clip.mp4?download=1',
                subtitle_url=base+'subtitles.srt' if rendered['caption_groups'] else None,
                thumbnail_url=base+'thumbnail.jpg' if (folder/'thumbnail.jpg').exists() else None)
            items.append(rendered)
            update(job,items=list(items))
        update(job,stage='Packaging batch download',progress=.97)
        manifest=dict(mode=body.mode,source_duration=data['duration'],target_duration=body.duration,
            category=body.category if body.mode=='highlights' else None,items=items,
            transcription_engine='faster-whisper' if words else None)
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        with ZipFile(output/'clips.zip','w',ZIP_STORED) as archive:
            archive.write(output/'manifest.json','manifest.json')
            for folder in sorted(output.glob('clip_*')):
                for name in ('clip.mp4','thumbnail.jpg','subtitles.srt','words.json'):
                    if (folder/name).is_file():archive.write(folder/name,f'{folder.name}/{name}')
        coverage=dict(start=0,end=data['duration'],duration=sum(c['end']-c['start'] for c in clips),
            complete=True,remainder=clips[-1]['end']-clips[-1]['start']) if body.mode=='full' else None
        update(job,status='completed',stage='Ready',progress=1,items=items,coverage=coverage,
            zip_url=f'/studio/jobs/{job["id"]}/zip',message=f'{len(items)} clips ready. '+('Entire source covered, including the final remainder.' if body.mode=='full' else 'AI-selected transcript highlights; not full source coverage.'))
    except Exception as error:
        logging.exception('Local Studio job failed')
        update(job,status='failed',stage='Stopped',error=str(error),message='Job stopped. Partial results, if any, remain available; no completion was claimed.')


def register(app,existing_loader):
    @app.get('/studio')
    def page():return FileResponse(ROOT/'studio.html',headers={'Cache-Control':'no-store'})

    @app.get('/studio/capabilities')
    def capabilities():
        try:import cv2;vision=True
        except ImportError:vision=False
        from .transcription import status as speech_status
        return dict(ai=studio_ai.status(),transcription=speech_status(),face_detection=vision,categories=studio_ai.CATEGORIES,
            scene_transitions=True,emoji_font=Path('C:/Windows/Fonts/seguiemj.ttf').is_file())

    @app.post('/studio/sources')
    def upload(file: UploadFile=File(...)):
        source_id=uuid.uuid4().hex
        destination=SOURCES/(source_id+'.video')
        try:
            with destination.open('wb') as output:shutil.copyfileobj(file.file,output)
            info=studio_render.probe(destination)
            data=dict(id=source_id,studio_source=True,source=str(destination),source_name=file.filename,**info)
            (SOURCES/(source_id+'.json')).write_text(json.dumps(data),encoding='utf-8')
            return {k:v for k,v in data.items() if k!='source'}
        except Exception:
            destination.unlink(missing_ok=True)
            raise HTTPException(400,'Could not read the uploaded video')

    @app.post('/studio/jobs',status_code=202)
    def generate(body:StudioRequest):
        if (body.mode=='highlights' or body.enhancements.hooks or body.enhancements.context or body.enhancements.emoji) and not studio_ai.status()['ready']:
            raise HTTPException(503,'Local text-analysis model is not installed. AI Highlights/hooks/context/emoji require approval and a local model; Full Video works without it.')
        try:data=_load_source(body.source_id,existing_loader)
        except Exception:raise HTTPException(404,'Source not found. Upload it again.')
        if body.mode=='full':
            try:full_ranges(data['duration'],body.duration)
            except ValueError as error:raise HTTPException(422,str(error))
        job=dict(id=uuid.uuid4().hex,status='queued',stage='Queued',progress=0,items=[],warnings=[],
            created=time.time(),request=body.model_dump(),source_name=data.get('source_name','Video'),source_duration=data['duration'])
        with _lock:_jobs[job['id']]=job;_save(job)
        _pool.submit(_worker,job,body,data)
        return dict(id=job['id'],status_url='/studio/jobs/'+job['id'])

    @app.get('/studio/jobs/{job_id}')
    def job_status(job_id:str):
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise HTTPException(404)
        with _lock:
            if job_id in _jobs:return json.loads(json.dumps(_jobs[job_id]))
        path=JOBS/(job_id+'.json')
        if not path.is_file():raise HTTPException(404)
        data=json.loads(path.read_text(encoding='utf-8'))
        if data['status'] in ('running','queued'):
            data.update(status='failed',stage='Interrupted',error='Local server restarted. Generate a new job to retry.')
        return data

    @app.get('/studio/jobs/{job_id}/zip')
    def batch(job_id:str):
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise HTTPException(404)
        path=JOBS/job_id/'clips.zip'
        if not path.is_file():raise HTTPException(404,'Batch ZIP is not ready')
        return FileResponse(path,filename='clipforge-clips.zip',media_type='application/zip')

    @app.get('/studio/files/{job_id}/{clip_id}/{filename}')
    def asset(job_id:str,clip_id:str,filename:str,download:bool=False):
        if not re.fullmatch('[a-f0-9]{32}',job_id) or not re.fullmatch('clip_[0-9]{3,4}',clip_id):raise HTTPException(404)
        if filename not in ('clip.mp4','thumbnail.jpg','subtitles.srt','words.json'):raise HTTPException(404)
        path=JOBS/job_id/clip_id/filename
        if not path.is_file():raise HTTPException(404)
        mime={'clip.mp4':'video/mp4','thumbnail.jpg':'image/jpeg','subtitles.srt':'text/plain','words.json':'application/json'}[filename]
        return FileResponse(path,filename=f'{clip_id}_{filename}' if download else None,media_type=mime)
