"""Media clipping by transcript words (Text-to-Clip). No production imports or credentials of its own;
transcribes with OpenAI Whisper when OPENAI_API_KEY is set, otherwise with the offline model."""
import asyncio
import hashlib
import json
import math
import logging
from pathlib import Path
import re
import shutil
import uuid
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import media, openai_speech, link_media
from .transcription import MODEL_PATH, transcribe

ROOT = Path(__file__).resolve().parent
from .resource_paths import DATA
STORE = DATA/'real_transcripts'
STORE.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='ClipForge local speech clipping')
app.mount('/media/previews', StaticFiles(directory=media.PREVIEW_DIR), name='previews')

class Selection(BaseModel):
    first: int = Field(ge=0)
    last: int = Field(ge=0)
    padding: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)

class AutoRequest(BaseModel):
    duration: float = Field(default=30, ge=1, le=600, allow_inf_nan=False)
    count: int = Field(default=3, ge=1, le=20)

def digest(path):
    with open(path,'rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def load(video_id):
    if not re.fullmatch('[a-f0-9]{32}',video_id):
        raise HTTPException(404,'Video not found')
    path=STORE/(video_id+'.json')
    if not path.is_file():
        raise HTTPException(404,'Upload and transcribe this video again. Old mock transcripts are not accepted.')
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('engine') not in ('faster-whisper',openai_speech.ENGINE) or data.get('schema') != 2:
        raise HTTPException(409,'Transcript is not from speech recognition; transcribe again')
    if not Path(data['source']).is_file() or digest(data['source']) != data['source_sha256']:
        raise HTTPException(409,'Source video changed. Transcribe the current source again.')
    return data

@app.post('/api/videos')
def upload(file: UploadFile = File(...)):
    cloud=openai_speech.enabled()
    if not cloud and not (MODEL_PATH/'model.bin').is_file():
        raise HTTPException(503,'Local speech model is missing. Model download requires your approval. No mock transcript will be substituted.')
    video_id=uuid.uuid4().hex
    source=Path(media.UPLOAD_DIR)/(video_id+Path(file.filename or 'video.mp4').suffix)
    try:
        with source.open('wb') as output:shutil.copyfileobj(file.file,output)
    except Exception:
        source.unlink(missing_ok=True);raise HTTPException(400,'Could not save the uploaded video')
    return transcribe_source(video_id,source,file.filename)

@app.post('/api/videos/url')
def from_link(body: link_media.Link):
    """A YouTube, TikTok or Instagram video, downloaded and transcribed like an upload."""
    if not openai_speech.enabled() and not (MODEL_PATH/'model.bin').is_file():
        raise HTTPException(503,'Local speech model is missing. Model download requires your approval. No mock transcript will be substituted.')
    video_id=uuid.uuid4().hex
    source=link_media.fetch(body.link,Path(media.UPLOAD_DIR)/(video_id+'.mp4'))
    return transcribe_source(video_id,source,link_media.name(body.link))

def transcribe_source(video_id,source,name):
    """Transcribe a saved video into STORE; the source is deleted if this fails."""
    cloud=openai_speech.enabled();usd=0
    audio=Path(media.TMP_DIR)/(video_id+'.wav')
    try:
        duration=media.ffprobe_duration(str(source))
        if not duration or not math.isfinite(duration):raise HTTPException(400,'Unreadable video')
        if cloud:
            if not openai_speech.has_audio(source):raise HTTPException(400,'Cannot extract speech audio from this video')
            transcript,usd=openai_speech.transcript(source,duration)
        else:
            code,_=media.run(['ffmpeg','-v','error','-y','-i',str(source),'-map','0:a:0',
                '-vn','-af','aresample=async=1:first_pts=0','-ar','16000','-ac','1','-c:a','pcm_s16le',str(audio)])
            if code:raise HTTPException(400,'Cannot extract speech audio from this video')
            transcript=transcribe(audio)
        words=transcript['words']
        if not words:raise HTTPException(422,'No speech detected. No mock transcript was generated.')
        if any(not 0 <= w['start'] < w['end'] <= duration+.1 for w in words):
            raise HTTPException(422,'Speech alignment falls outside the video; cannot safely clip it')
        for w in words:w['end']=min(duration,w['end'])
        data=dict(**transcript,video_id=video_id,schema=2,duration=duration,source=str(source),
                  source_sha256=digest(source),source_name=name,cost_usd=usd)
        (STORE/(video_id+'.json')).write_text(json.dumps(data),encoding='utf-8')
        return {k:v for k,v in data.items() if k!='source'}
    except HTTPException:
        source.unlink(missing_ok=True)
        raise
    except (RuntimeError,ImportError) as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(503,str(exc))
    except Exception:
        logging.exception('Video transcription failed')
        source.unlink(missing_ok=True)
        raise HTTPException(500,('OpenAI' if cloud else 'Local')+' transcription failed. No fallback transcript was used.')
    finally:audio.unlink(missing_ok=True)

def extract(data,first,last,padding=0):
    words=data['words']
    if first > last or last >= len(words):raise HTTPException(422,'Invalid word selection')
    word_start,word_end=words[first]['start'],words[last]['end']
    start,end=max(0,word_start-padding),min(data['duration'],word_end+padding)
    filename=uuid.uuid4().hex+'.mp4'
    output=Path(media.PREVIEW_DIR)/filename
    # Seek before -i: jumps straight to the clip instead of decoding everything before it
    # (a clip at minute 20 took 48 s). Still exact, because the clip is re-encoded.
    code,_=media.run(['ffmpeg','-v','error','-y','-ss',f'{start:.6f}','-i',data['source'],
        '-t',f'{end-start:.6f}','-map','0:v:0','-map','0:a:0',
        '-c:v','libx264','-preset','veryfast','-crf','20','-c:a','aac',
        '-b:a','192k','-movflags','+faststart',str(output)])
    if code:
        output.unlink(missing_ok=True)
        raise HTTPException(500,'FFmpeg could not render the selected section')
    return dict(first=first,last=last,start=start,end=end,word_start=word_start,word_end=word_end,
        padding=padding,duration_seconds=end-start,actual_duration=media.ffprobe_duration(str(output)),
        text=' '.join(w['word'] for w in words[first:last+1]),
        preview_url='/media/previews/'+filename,download_url='/api/clips/'+filename)

@app.post('/api/videos/{video_id}/selection')
async def selection(video_id: str, body: Selection):
    data=await asyncio.to_thread(load,video_id)
    return await asyncio.to_thread(extract,data,body.first,body.last,body.padding)

@app.get('/api/videos/{video_id}/source')
def source_video(video_id: str):
    return FileResponse(load(video_id)['source'],media_type='video/mp4')

@app.post('/api/videos/{video_id}/auto')
async def auto(video_id: str, body: AutoRequest):
    data=await asyncio.to_thread(load,video_id)
    words=data['words']
    starts=[0]+[i+1 for i,w in enumerate(words[:-1]) if re.search(r'[.!?]["\u201d\']?$',w['word'])]
    ends=[i for i,w in enumerate(words) if re.search(r'[.!?]["\u201d\']?$',w['word'])]+[len(words)-1]
    options=[]
    for first in starts:
        start=words[first]['start']
        possible=[last for last in ends if last>=first and abs(words[last]['end']-start-body.duration)<=max(1,body.duration*.2)]
        if possible:last=min(possible,key=lambda j:abs(words[j]['end']-start-body.duration))
        else:
            possible=[j for j in range(first,len(words)) if words[j]['end']-start<=body.duration]
            if not possible:continue
            last=possible[-1]
        if words[last]['end']-start<body.duration*.7:continue
        options.append((first,last))
    chosen=[]
    for first,last in options:
        if any(words[first]['start']<words[b]['end'] and words[last]['end']>words[a]['start'] for a,b in chosen):continue
        chosen.append((first,last))
        if len(chosen)==body.count:break
    if not chosen:raise HTTPException(422,'Not enough spoken content for this duration')
    clips=[await asyncio.to_thread(extract,data,a,b) for a,b in chosen]
    return dict(items=clips,message=f'Generated {len(clips)} of {body.count} clips. Local speech transcript; mock editorial ranking. Sentence boundaries may adjust duration.')

@app.get('/api/clips/{filename}')
def download(filename: str):
    if not re.fullmatch(r'[a-f0-9]{32}\.mp4',filename):raise HTTPException(404)
    path=Path(media.PREVIEW_DIR)/filename
    if not path.is_file():raise HTTPException(404)
    return FileResponse(path,filename=filename,media_type='video/mp4')

@app.get('/')
def index():return FileResponse(ROOT/'index.html',headers={'Cache-Control':'no-store'})

@app.get('/fixture')
def fixture():
    path=ROOT/'data/known_dialogue.mp4'
    if not path.is_file():raise HTTPException(404,'Known-dialogue video has not been generated')
    return FileResponse(path,media_type='video/mp4',filename='known-dialogue.mp4')

@app.get('/health')
def health():
    return dict(ok=True,mode='local-only',transcript='faster-whisper',ai='mock editorial ranking',
        model=MODEL_PATH.name,model_ready=(MODEL_PATH/'model.bin').is_file(),cloud_connections=False)

# Additive local Studio. Existing transcript selection and auto routes are unchanged.
from .studio import register as register_studio
register_studio(app,load)
