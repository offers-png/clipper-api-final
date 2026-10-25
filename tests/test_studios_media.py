"""Actual FFmpeg media/HTTP integration checks; no AI-generated output is mocked."""
import json
import os
import math
from pathlib import Path
import struct
import subprocess
import time
import unittest
import uuid
import requests

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(os.environ.get('CLIPFORGE_TEST_VIDEO',str(ROOT/'fixtures/actual-video-20s.mp4')))
FIX=ROOT/'clipforge_studios/data/test-fixtures'
FIX.mkdir(exist_ok=True,parents=True)
BASE=os.environ.get('CLIPFORGE_TEST_BASE','http://localhost:5175/studios-api')
RESULTS={}

def ff(args):
    return subprocess.check_output(['ffmpeg','-v','error','-y',*args],stderr=subprocess.STDOUT)

def upload(path):
    with path.open('rb') as f:r=requests.post(BASE+'/editor/assets',files={'file':(path.name,f)},timeout=60)
    assert r.status_code==200,r.text
    return r.json()

def render(body):
    r=requests.post(BASE+'/editor/jobs',json=body,timeout=30);assert r.status_code==202,r.text
    jid=r.json()['id']
    for _ in range(600):
        job=requests.get(BASE+'/editor/jobs/'+jid,timeout=15).json()
        if job['status'] not in ('queued','running'):break
        time.sleep(.2)
    assert job['status']=='completed',job
    r=requests.get(BASE+job['download_url'],timeout=60);assert r.status_code==200 and b'ftyp' in r.content[:32]
    path=FIX/(jid+'.mp4');path.write_bytes(r.content)
    job['test_path']=str(path)
    return job,path

def samples(path,start=0,duration=None):
    args=['-ss',str(start),'-i',str(path)]
    if duration:args+=['-t',str(duration)]
    raw=ff([*args,'-vn','-ar','16000','-ac','1','-f','s16le','-'])
    return struct.unpack('<'+'h'*(len(raw)//2),raw)

def rms(s):return math.sqrt(sum(x*x for x in s)/max(1,len(s)))
def frequency(s,hz):
    return 2*abs(sum(v*complex(math.cos(2*math.pi*hz*i/16000),-math.sin(2*math.pi*hz*i/16000)) for i,v in enumerate(s)))/len(s)
def pixel(path,t,x,y):
    return list(ff(['-ss',str(t),'-i',str(path),'-frames:v','1','-vf',f'crop=2:2:{x}:{y},scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])[:3])


def studio_job(body):
    r=requests.post(BASE+'/studio/jobs',json=body,timeout=20);assert r.status_code==202,r.text
    for _ in range(900):
        job=requests.get(BASE+'/studio/jobs/'+r.json()['id'],timeout=15).json()
        if job['status'] not in ('queued','running'):break
        time.sleep(.3)
    assert job['status']=='completed',job
    for item in job['items']:
        assert requests.get(BASE+item['preview_url'],timeout=60).status_code==200
        if item.get('thumbnail_url'):assert requests.get(BASE+item['thumbnail_url'],timeout=30).status_code==200
    from zipfile import ZipFile
    from io import BytesIO
    z=requests.get(BASE+job['zip_url'],timeout=60)
    assert z.status_code==200 and len(ZipFile(BytesIO(z.content)).namelist())>=len(job['items'])
    return job

@unittest.skipUnless(SOURCE.is_file(),"Set CLIPFORGE_TEST_VIDEO to an actual local spoken video; no mock transcript is used")
class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.video=upload(SOURCE)
        ff(['-i',str(SOURCE),'-frames:v','1',str(FIX/'actual-frame.png')])
        ff(['-f','lavfi','-i','color=c=red:s=320x180','-frames:v','1',str(FIX/'red.png')])
        ff(['-f','lavfi','-i','color=c=cyan:s=100x100','-frames:v','1',str(FIX/'cyan.png')])
        for name,hz in [('music',220),('effect',880)]:ff(['-f','lavfi','-i',f'sine=frequency={hz}:duration=4','-c:a','pcm_s16le',str(FIX/(name+'.wav'))])
        cls.frame=upload(FIX/'actual-frame.png');cls.red=upload(FIX/'red.png');cls.overlay=upload(FIX/'cyan.png')
        cls.music=upload(FIX/'music.wav');cls.effect=upload(FIX/'effect.wav')

    def test_01_trim_split_reorder_images_overlays_captions_fades(self):
        v=self.video['id']
        body=dict(segments=[dict(asset_id=self.frame['id'],end=.5),dict(asset_id=v,start=1.5,end=2.5),dict(asset_id=v,start=.5,end=1.5),dict(asset_id=self.red['id'],end=.5)],
            overlays=[dict(asset_id=self.overlay['id'],start=.75,end=2.5,x=30,y=30,width=100,height=100)],
            texts=[dict(text="Actual local caption: 50% {test} 'quote'",start=.5,end=2.5,x=300,y=600,size=30)],
            fade_in=.15,fade_out=.15,original_volume=.5)
        job,path=render(body)
        self.assertAlmostEqual(job['duration'],3,delta=.12)
        cyan=pixel(path,1,50,50);self.assertGreater(cyan[1],200);self.assertGreater(cyan[2],200);self.assertLess(cyan[0],30)
        red=pixel(path,2.75,600,300);self.assertGreater(red[0],200);self.assertLess(red[1],30)
        self.assertLess(max(pixel(path,0,640,300)),8)  # Actual black first fade frame.
        pixels=ff(['-ss','1','-i',str(path),'-frames:v','1','-vf','crop=900:60:300:600','-pix_fmt','rgb24','-f','rawvideo','-'])
        self.assertGreater(sum(min(pixels[i:i+3])>215 for i in range(0,len(pixels),3)),200)
        # Reordered segment 2 must show source time 2.0 at timeline time 1.0.
        expected=FIX/'expected.mp4';ff(['-ss','2','-i',str(SOURCE),'-t','0.1','-vf','scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2','-an',str(expected)])
        actual=pixel(path,1,640,300);wanted=pixel(expected,0,640,300)
        self.assertLess(max(abs(a-b) for a,b in zip(actual,wanted)),35)
        RESULTS['timeline']=dict(job=job,cyan_pixel=cyan,outro_pixel=red,reordered_pixel=actual,expected_pixel=wanted)

    def test_02_original_volume_and_mute(self):
        results=[]
        for vol,mute in [(1,False),(.5,False),(1,True)]:
            job,p=render(dict(segments=[dict(asset_id=self.video['id'],start=0,end=2)],original_volume=vol,mute_original=mute))
            results.append(dict(volume=vol,mute=mute,rms=rms(samples(p)),job=job))
        self.assertGreater(results[0]['rms'],100)
        self.assertAlmostEqual(results[1]['rms']/results[0]['rms'],.5,delta=.08)
        self.assertLess(results[2]['rms'],2)
        RESULTS['original_audio']=results

    def test_03_music_effect_volume_and_timing(self):
        levels=[]
        for volume in [.2,.8]:
            job,p=render(dict(segments=[dict(asset_id=self.video['id'],end=3)],mute_original=True,tracks=[
                dict(asset_id=self.music['id'],start=0,duration=3,volume=volume,role='music',loop=True),
                dict(asset_id=self.effect['id'],start=1,duration=.5,volume=.5,role='effect')]))
            before=samples(p,.2,.5);during=samples(p,1.1,.25)
            music=frequency(before,220);effect=frequency(during,880)
            self.assertGreater(effect,1000);self.assertLess(frequency(before,880),50)
            levels.append(dict(volume=volume,music_amplitude=music,effect_amplitude=effect,job=job))
        self.assertAlmostEqual(levels[1]['music_amplitude']/levels[0]['music_amplitude'],4,delta=.3)
        RESULTS['audio_tracks']=levels

    def test_04_imports_and_real_provider_unavailable(self):
        caps=requests.get(BASE+'/image-video/capabilities').json();self.assertFalse(caps['available'])
        for motion in ['Cinematic','Natural movement','Camera zoom','Camera pan','Animated character','Custom prompt']:
            r=requests.post(BASE+'/image-video/jobs',json=dict(image_id=self.frame['id'],duration=5,ratio='9:16',motion=motion,prompt='Gentle movement'))
            self.assertEqual(r.status_code,503,r.text)
        for url in ['https://example.com/video.mp4','/editor/files/../../app.py','C:/Windows/win.ini']:
            self.assertEqual(requests.post(BASE+'/editor/import',json={'url':url}).status_code,422)
        # Create actual source/clip outputs in this packaged API, not development IDs.
        with SOURCE.open('rb') as f:
            source=requests.post(BASE+'/studio/sources',files={'file':('actual.mp4',f)}).json()
        manual=requests.post(BASE+'/local-original/manual',json={'source_id':source['id'],'sections':[{'start':0,'end':1}]}).json()
        full=studio_job(dict(source_id=source['id'],mode='full',duration=7,enhancements=dict(subtitles=False,vertical=False)))
        with SOURCE.open('rb') as f:
            speech=requests.post(BASE+'/api/videos',files={'file':('actual.mp4',f)},timeout=240).json()
        clip=requests.post(BASE+'/api/videos/'+speech['video_id']+'/selection',json=dict(first=0,last=6,padding=0)).json()
        urls=[manual['items'][0]['preview_url'],clip['preview_url'],full['items'][0]['preview_url']]
        for url in urls:
            r=requests.post(BASE+'/editor/import',json={'url':url});self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['kind'],'video')
        self.assertTrue(full['coverage']['complete']);self.assertEqual(len(full['items']),3)
        ai=studio_job(dict(source_id=source['id'],mode='highlights',duration=10,count=2,category='best',enhancements=dict(subtitles=True,animated=True,hooks=True,context=True,vertical=True,thumbnail=True)))
        self.assertGreaterEqual(len(ai['items']),1)
        for count in range(1,6):
            r=requests.post(BASE+'/local-original/manual',json=dict(source_id=source['id'],sections=[dict(start=i*2/count,end=(i+1)*2/count) for i in range(count)]),timeout=240)
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()['items']),count)
        RESULTS['clipping_regression']=dict(manual_counts=[1,2,3,4,5],transcript_words=len(speech['words']),selected_start=clip['start'],selected_end=clip['end'],full_durations=[i['actual_duration'] for i in full['items']],ai_clips=len(ai['items']))
        RESULTS['provider']=caps;RESULTS['workflow_imports']=urls

    def test_05_provider_contract_without_synthetic_success(self):
        from clipforge_studios.backend import image_video
        calls=[]
        class ContractProbe:
            def status(self):return dict(available=True,name='contract probe only',durations=[5],ratios=['9:16'])
            def generate(self,path,request):
                calls.append((path,request.model_dump()))
                raise RuntimeError('Contract probe: no AI provider connected, no video synthesized')
        previous=image_video.provider
        try:
            image_video.provider=ContractProbe()
            body=image_video.Generation(image_id=self.frame['id'],prompt='Gentle motion',duration=5,ratio='9:16',motion='Natural movement')
            job=dict(id=uuid.uuid4().hex,status='queued')
            image_video.worker(job,body)
            self.assertEqual(job['status'],'failed');self.assertEqual(len(calls),1)
            self.assertTrue(calls[0][0].is_file());self.assertEqual(calls[0][1],body.model_dump())
            self.assertFalse((image_video.editor.JOBS/job['id']/'finished.mp4').exists())
            RESULTS['provider_contract']=dict(parameters_forwarded=True,status=job['status'],actual_generation=False)
        finally:image_video.provider=previous

if __name__=='__main__':
    try:unittest.main(verbosity=2)
    finally:(FIX/'results.json').write_text(json.dumps(RESULTS,indent=2),encoding='utf-8')
