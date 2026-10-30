"""Additive, offline timeline editor. All media paths are server-owned IDs."""
from concurrent.futures import ThreadPoolExecutor
import json
import mimetypes
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid
from typing import Literal
from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from . import media, studio, link_media

from .resource_paths import DATA
ROOT=DATA/'editor'
ASSETS=ROOT/'assets'; JOBS=ROOT/'jobs'
for p in (ASSETS,JOBS):p.mkdir(parents=True,exist_ok=True)
POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='timeline-render')
STATE={}; STATE_LOCK=threading.Lock()
SIZES={'16:9':(1280,720),'9:16':(720,1280),'1:1':(720,720)}
# Text fonts offered in the editor; the browser preview uses the same family names.
FONTS={'Arial':'arial.ttf','Impact':'impact.ttf','Georgia':'georgia.ttf','Verdana':'verdana.ttf','Courier New':'cour.ttf'}
FRAMES=ASSETS/'frames';FRAMES.mkdir(exist_ok=True)

def probe(path):
    r=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,timeout=30)
    if r.returncode:raise ValueError('Unsupported or unreadable media')
    d=json.loads(r.stdout);v=next((s for s in d['streams'] if s['codec_type']=='video'),None)
    audio=any(s['codec_type']=='audio' for s in d['streams'])
    if not v and not audio:raise ValueError('No usable image, video or audio stream')
    image=bool(v and v.get('codec_name') in ('png','mjpeg','webp') and not audio and float(d.get('format',{}).get('duration',0))==0)
    if v and v.get('width',0)*v.get('height',0)>25000000:raise ValueError('Image/video exceeds 25 megapixels')
    return dict(kind='image' if image else 'video' if v else 'audio',audio=audio,codec=v.get('codec_name') if v else None,
        duration=float(d.get('format',{}).get('duration',0)),width=v.get('width') if v else None,height=v.get('height') if v else None)

def asset(asset_id):
    if not re.fullmatch('[a-f0-9]{32}',asset_id):raise ValueError('Invalid asset ID')
    p=ASSETS/(asset_id+'.json')
    if not p.is_file():raise ValueError('Media asset not found')
    return json.loads(p.read_text(encoding='utf-8'))

def save_asset(path,name):
    info=probe(path);aid=uuid.uuid4().hex
    target=ASSETS/(aid+'.media');shutil.copyfile(path,target)
    d=dict(id=aid,name=name,path=str(target.resolve()),**info)
    (ASSETS/(aid+'.json')).write_text(json.dumps(d),encoding='utf-8')
    return public(d)

def public(d):return {k:v for k,v in d.items() if k!='path'}

def import_path(url):
    # No network fetching, arbitrary filesystem paths, URLs or credentials.
    mappings={'/media/previews/':Path(media.PREVIEW_DIR),'/media/exports/':Path(media.EXPORT_DIR),
        '/studio/files/':studio.JOBS,'/editor/files/':JOBS}
    if '?' in url:url=url.split('?',1)[0]
    for prefix,folder in mappings.items():
        if url.startswith(prefix):
            suffix=url[len(prefix):]
            if not re.fullmatch(r'[a-zA-Z0-9_./-]+',suffix):break
            path=(folder/suffix).resolve()
            if path.is_relative_to(folder.resolve()) and path.is_file() and path.suffix.lower()=='.mp4':return path
    raise ValueError('Only existing local ClipForge MP4 outputs may be imported')

class Import(BaseModel):url:str=Field(max_length=500)
class Segment(BaseModel):
    asset_id:str
    start:float=Field(default=0,ge=0,allow_inf_nan=False)
    end:float=Field(default=3,gt=0,le=600,allow_inf_nan=False)
    volume:float=Field(default=1,ge=0,le=2,allow_inf_nan=False)
    mute:bool=False
class Track(BaseModel):
    asset_id:str
    start:float=Field(default=0,ge=0,le=600,allow_inf_nan=False)
    duration:float=Field(gt=0,le=600,allow_inf_nan=False)
    volume:float=Field(default=.5,ge=0,le=2,allow_inf_nan=False)
    loop:bool=False
    role:Literal['music','effect']='music'
class Overlay(BaseModel):
    asset_id:str
    start:float=Field(default=0,ge=0,allow_inf_nan=False)
    end:float=Field(gt=0,le=600,allow_inf_nan=False)
    x:int=Field(default=30,ge=0,le=1280)
    y:int=Field(default=30,ge=0,le=1280)
    width:int=Field(default=200,ge=16,le=1280)
    height:int=Field(default=200,ge=16,le=1280)
class Text(BaseModel):
    text:str=Field(min_length=1,max_length=500)
    start:float=Field(default=0,ge=0,allow_inf_nan=False)
    end:float=Field(gt=0,le=600,allow_inf_nan=False)
    x:int=Field(default=40,ge=0,le=1280)
    y:int=Field(default=550,ge=0,le=1280)
    size:int=Field(default=38,ge=12,le=150)
    color:str=Field(default='#ffffff',pattern='^#[0-9a-fA-F]{6}$')
    font:Literal['Arial','Impact','Georgia','Verdana','Courier New']='Arial'
class Timeline(BaseModel):
    segments:list[Segment]=Field(min_length=1,max_length=30)
    tracks:list[Track]=Field(default_factory=list,max_length=12)
    overlays:list[Overlay]=Field(default_factory=list,max_length=12)
    texts:list[Text]=Field(default_factory=list,max_length=30)
    ratio:Literal['16:9','9:16','1:1']='16:9'
    original_volume:float=Field(default=1,ge=0,le=2,allow_inf_nan=False)
    mute_original:bool=False
    fade_in:float=Field(default=0,ge=0,le=5,allow_inf_nan=False)
    fade_out:float=Field(default=0,ge=0,le=5,allow_inf_nan=False)

def validate(body):
    total=0
    for s in body.segments:
        a=asset(s.asset_id)
        if a['kind']=='audio':raise ValueError('Use audio assets as music or sound tracks')
        if s.end-s.start<.1:raise ValueError('Each segment must be at least 0.1 seconds')
        if a['kind']=='video' and s.end>a['duration']+.03:raise ValueError('Trim exceeds video duration')
        if a['kind']=='image' and s.start!=0:raise ValueError('Image segments start at zero; end sets their display duration')
        total+=s.end-s.start
    if total>600:raise ValueError('Local timeline limit is 600 seconds')
    if body.fade_in+body.fade_out>total:raise ValueError('Combined fades exceed timeline duration')
    for t in body.tracks:
        if not asset(t.asset_id)['audio']:raise ValueError('Audio track has no audio stream')
        if t.start>=total or t.start+t.duration>total+.03:raise ValueError('Audio track must stay within the timeline')
    w,h=SIZES[body.ratio]
    for o in [*body.overlays,*body.texts]:
        if o.end<=o.start or o.end>total+.03:raise ValueError('Overlay/caption timing must stay within the timeline')
        if o.x>=w or o.y>=h:raise ValueError('Overlay position is outside the selected canvas')
    for o in body.overlays:
        if asset(o.asset_id)['kind']!='image':raise ValueError('Image overlay requires an image asset')
    return total

EMOJI_FONT=Path(__file__).parent/'fonts'/'NotoColorEmoji.ttf'

def is_emoji(ch):
    o=ord(ch);return o>=0x1F000 or 0x2600<=o<=0x27BF or 0x2B00<=o<=0x2BFF

def text_image(t,path):
    """The text as a picture, drawn like the browser preview: font, size and color on a black 45% box with an
    8px border, top-left text at x,y, and full-color emoji (drawtext can't draw emoji). False without Pillow or the
    font file; drawtext is used then."""
    try:from PIL import Image,ImageDraw,ImageFont
    except ImportError:return False
    file=Path('C:/Windows/Fonts')/FONTS[t.font]
    if not file.is_file():return False
    font=ImageFont.truetype(str(file),t.size);ascent,descent=font.getmetrics();line=ascent+descent
    emoji=ImageFont.truetype(str(EMOJI_FONT),109) if EMOJI_FONT.is_file() else None  # bitmap size
    tall=round(line*.95);gap=round(t.size*.08)
    def glyph(ch):
        drawn=Image.new('RGBA',(160,160));ImageDraw.Draw(drawn).text((0,0),ch,font=emoji,embedded_color=True)
        if not drawn.getbbox():return None
        g=drawn.crop(drawn.getbbox());return g.resize((max(1,round(g.width*tall/g.height)),tall),Image.LANCZOS)
    lines=[]
    for raw in t.text.split('\n'):
        pieces=[];joined=False
        for ch in raw.replace('\ufe0f',''):
            if ch=='\u200d':joined=True;continue
            if joined:joined=False;continue  # no shaping engine: the first emoji of a joined sequence
            if emoji and is_emoji(ch):pieces.append(glyph(ch))
            elif pieces and isinstance(pieces[-1],str):pieces[-1]+=ch
            else:pieces.append(ch)
        lines.append([p for p in pieces if p is not None])
    width=lambda pieces:sum(font.getlength(p) if isinstance(p,str) else p.width+gap for p in pieces)
    image=Image.new('RGBA',(max([round(width(p)) for p in lines]+[1])+16,line*len(lines)+16),(0,0,0,115))
    draw=ImageDraw.Draw(image)
    for row,pieces in enumerate(lines):
        x,y=8,8+row*line
        for p in pieces:
            if isinstance(p,str):draw.text((x,y),p,font=font,fill=t.color);x+=font.getlength(p)
            else:image.alpha_composite(p,(round(x),y+(line-tall)//2));x+=p.width+gap
    image.save(path);return True

def run(args,cwd):
    r=subprocess.run(['ffmpeg','-v','error','-y',*args],cwd=cwd,capture_output=True,text=True,timeout=900)
    if r.returncode:raise RuntimeError('FFmpeg could not render: '+r.stderr[-1800:])

def render(job,body):
    folder=JOBS/job['id'];folder.mkdir(exist_ok=True)
    try:
        total=validate(body);w,h=SIZES[body.ratio];paths=[]
        for i,s in enumerate(body.segments):
            job.update(status='running',stage=f'Normalizing segment {i+1}/{len(body.segments)}',progress=.6*i/len(body.segments))
            a=asset(s.asset_id);duration=s.end-s.start
            args=['-loop','1','-framerate','30','-i',a['path']] if a['kind']=='image' else ['-ss',str(s.start),'-i',a['path']]
            has_audio=a['kind']=='video' and a['audio']
            if not has_audio:args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
            vf=f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p'
            vol=0 if s.mute or body.mute_original else s.volume*body.original_volume
            name=f'part_{i:03d}.mp4';paths.append(name)
            run([*args,'-map','0:v:0','-map','0:a:0' if has_audio else '1:a:0','-t',str(duration),'-vf',vf,
                '-af',f'aresample=48000,aformat=channel_layouts=stereo,volume={vol},apad,atrim=duration={duration},asetpts=PTS-STARTPTS',
                '-c:v','libx264','-preset','ultrafast','-crf','23','-c:a','aac','-ar','48000','-ac','2',name],folder)
        (folder/'concat.txt').write_text('\n'.join(f"file '{p}'" for p in paths),encoding='utf-8')
        job.update(stage='Combining ordered segments',progress=.65)
        run(['-f','concat','-safe','0','-i','concat.txt','-c','copy','joined.mp4'],folder)
        args=['-i','joined.mp4'];filters=['[0:v]setpts=PTS-STARTPTS[vbase]','[0:a]asetpts=PTS-STARTPTS[abase]'];v='vbase';idx=1
        for n,o in enumerate(body.overlays):
            args+=['-loop','1','-framerate','30','-i',asset(o.asset_id)['path']]
            filters.append(f'[{idx}:v]scale={o.width}:{o.height}:force_original_aspect_ratio=decrease,format=rgba[ov{n}]')
            filters.append(f'[{v}][ov{n}]overlay=x={o.x}:y={o.y}:enable=\'between(t,{o.start},{o.end})\':eof_action=repeat[v{n}]')
            v=f'v{n}';idx+=1
        for n,t in enumerate(body.texts):
            if text_image(t,folder/f'text_{n}.png'):  # as the preview draws it, emoji included
                args+=['-loop','1','-framerate','30','-i',f'text_{n}.png']
                filters.append(f'[{idx}:v]format=rgba[tx{n}]')
                filters.append(f"[{v}][tx{n}]overlay=x={t.x-8}:y={t.y-8}:enable='between(t,{t.start},{t.end})':eof_action=repeat[txt{n}]")
                v=f'txt{n}';idx+=1;continue
            (folder/f'text_{n}.txt').write_text(t.text,encoding='utf-8')
            file=FONTS[t.font]
            font=f"fontfile='C\\:/Windows/Fonts/{file}'" if Path('C:/Windows/Fonts',file).is_file() else f"font='{t.font}'"
            filters.append(f"[{v}]drawtext={font}:textfile=text_{n}.txt:expansion=none:fontsize={t.size}:fontcolor={t.color}:x={t.x}:y={t.y}:box=1:boxcolor=black@0.45:boxborderw=8:enable='between(t,{t.start},{t.end})'[txt{n}]")
            v=f'txt{n}'
        if body.fade_in:filters.append(f'[{v}]fade=t=in:st=0:d={body.fade_in}[fi]');v='fi'
        if body.fade_out:filters.append(f'[{v}]fade=t=out:st={total-body.fade_out}:d={body.fade_out}[fo]');v='fo'
        audios=['abase']
        for n,t in enumerate(body.tracks):
            if t.loop:args+=['-stream_loop','-1']
            args+=['-i',asset(t.asset_id)['path']]
            filters.append(f'[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={t.duration},asetpts=PTS-STARTPTS,volume={t.volume},adelay={round(t.start*1000)}:all=1[aud{n}]')
            audios.append(f'aud{n}');idx+=1
        filters.append(''.join(f'[{a}]' for a in audios)+f'amix=inputs={len(audios)}:duration=first:normalize=0,alimiter=limit=0.95:level=0:latency=1,atrim=duration={total}[mixed]')
        a='mixed'
        if body.fade_in:filters.append(f'[{a}]afade=t=in:d={body.fade_in}[afi]');a='afi'
        if body.fade_out:filters.append(f'[{a}]afade=t=out:st={total-body.fade_out}:d={body.fade_out}[afo]');a='afo'
        job.update(stage='Rendering images, text, music and effects',progress=.75)
        run([*args,'-filter_complex',';'.join(filters),'-map',f'[{v}]' if v!='0:v' else '0:v','-map',f'[{a}]','-t',str(total),
             '-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','finished.mp4'],folder)
        job.update(status='completed',stage='Ready',progress=1,duration=probe(folder/'finished.mp4')['duration'],
            url=f'/editor/files/{job["id"]}/finished.mp4',download_url=f'/editor/files/{job["id"]}/finished.mp4?download=1')
    except Exception as e:job.update(status='failed',stage='Stopped',error=str(e))
    finally:
        (folder/'job.json').write_text(json.dumps(job),encoding='utf-8')
        # Preserve final render/manifest only, not duplicate temporary encodes.
        for p in folder.glob('part_*.mp4'):p.unlink(missing_ok=True)
        (folder/'joined.mp4').unlink(missing_ok=True)

def register(app):
    @app.post('/editor/assets')
    def upload(file:UploadFile=File(...)):
        temp=ASSETS/(uuid.uuid4().hex+'.upload')
        try:
            size=0
            with temp.open('wb') as output:
                while chunk:=file.file.read(1024*1024):
                    size+=len(chunk)
                    if size>512*1024**2:raise ValueError('Local upload limit is 512 MB')
                    output.write(chunk)
            return save_asset(temp,Path(file.filename or 'Media').name)
        except Exception as e:raise HTTPException(422,str(e))
        finally:temp.unlink(missing_ok=True)

    @app.post('/editor/assets/url')
    def from_link(body:link_media.Link):
        """A YouTube, TikTok or Instagram video added to the media library, within the upload limit."""
        temp=link_media.fetch(body.link,ASSETS/(uuid.uuid4().hex+'.upload'))
        try:
            if temp.stat().st_size>512*1024**2:raise HTTPException(413,'Local upload limit is 512 MB')
            return save_asset(temp,link_media.name(body.link))
        except HTTPException:raise
        except Exception as e:raise HTTPException(422,str(e))
        finally:temp.unlink(missing_ok=True)

    @app.post('/editor/import')
    def import_video(body:Import):
        try:
            p=import_path(body.url);return save_asset(p,p.name)
        except Exception as e:raise HTTPException(422,str(e))

    @app.get('/editor/assets/{asset_id}')
    def get_asset(asset_id:str):
        try:a=asset(asset_id)
        except Exception:raise HTTPException(404)
        mime={'png':'image/png','mjpeg':'image/jpeg','webp':'image/webp'}.get(a.get('codec'),'application/octet-stream') if a['kind']=='image' \
            else (mimetypes.guess_type(a.get('name',''))[0] or 'audio/mpeg') if a['kind']=='audio' else 'video/mp4'  # the preview plays audio too
        return FileResponse(a['path'],media_type=mime)

    @app.get('/editor/assets/{asset_id}/frame')
    def frame(asset_id:str,t:float=0,h:int=72):
        """One small video frame for the timeline's thumbnail strips (cached)."""
        try:a=asset(asset_id)
        except Exception:raise HTTPException(404)
        if a['kind']!='video' or not 24<=h<=240:raise HTTPException(404)
        t=round(min(max(0,t),max(0,a['duration']-.5)),1)  # the last frames can be missing
        target=FRAMES/f'{asset_id}_{round(t*10)}_{h}.jpg'
        if not target.is_file():
            r=subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',a['path'],'-frames:v','1','-vf',f'scale=-2:{h}','-q:v','5',str(target)],
                capture_output=True,timeout=60)
            if r.returncode or not target.is_file():raise HTTPException(404)
        return FileResponse(target,media_type='image/jpeg',headers={'Cache-Control':'private, max-age=86400'})

    @app.post('/editor/jobs',status_code=202)
    def create(body:Timeline):
        try:validate(body)
        except Exception as e:raise HTTPException(422,str(e))
        with STATE_LOCK:
            if any(j['status'] in ('queued','running') for j in STATE.values()):raise HTTPException(409,'An editor render is already running')
            job=dict(id=uuid.uuid4().hex,status='queued',stage='Queued',progress=0)
            STATE[job['id']]=job
        folder=JOBS/job['id'];folder.mkdir()
        (folder/'timeline.json').write_text(body.model_dump_json(),encoding='utf-8')
        POOL.submit(render,job,body)
        return job

    @app.get('/editor/jobs/{job_id}')
    def status(job_id:str):
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise HTTPException(404)
        if job_id in STATE:return dict(STATE[job_id])
        p=JOBS/job_id/'job.json'
        if not p.is_file():raise HTTPException(404)
        return json.loads(p.read_text(encoding='utf-8'))

    @app.get('/editor/files/{job_id}/finished.mp4')
    def finished(job_id:str,download:bool=False):
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise HTTPException(404)
        p=JOBS/job_id/'finished.mp4'
        if not p.is_file():raise HTTPException(404)
        return FileResponse(p,media_type='video/mp4',filename='clipforge-edited.mp4' if download else None)
