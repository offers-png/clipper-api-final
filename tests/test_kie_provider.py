"""Mocked KIE protocol tests. Live HTTP is prohibited for the entire suite.

Media bytes are local test fixtures, not evidence of real AI synthesis.
"""
import base64
from contextlib import ExitStack
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from clipforge_studios.backend import kie_provider as kie
from clipforge_studios.backend import editor, image_video, thumbnail_ai
from clipforge_studios.backend.video_provider_config import load
from tools.kie_image_smoke import main as smoke_main

KEY='contract-test'
CDN='https://tempfile.aiquickdraw.com/result'
PUBLIC_DNS=kie.public_dns

class Response:
    def __init__(self,body,status=200,headers=None):
        self.raw=body if isinstance(body,bytes) else json.dumps(body).encode()
        self.status_code=status;self.headers=headers or {};self.closed=False
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def iter_content(self,n):
        for i in range(0,len(self.raw),n):yield self.raw[i:i+n]

class Transport:
    def __init__(self,image,video=None,states=None):self.image=image;self.video=video;self.calls=[];self.states=list(states or ['waiting','generating','success']);self.task_count=0
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        if url==kie.UPLOAD:return Response(dict(code=200,data={'downloadUrl':CDN+'/reference.png'}))
        if url==kie.API+'/createTask':self.task_count+=1;return Response(dict(code=200,data={'taskId':'task-contract'}))
        if url==kie.API+'/recordInfo':
            state=self.states.pop(0) if self.states else 'success'
            return Response(dict(code=200,data={'state':state,'resultJson':json.dumps({'resultUrls':[CDN+('/video.mp4' if self.video else '/image.png')]})}))
        if url==CDN+'/image.png':return Response(self.image)
        if url==CDN+'/video.mp4':return Response(self.video)
        raise AssertionError('Unexpected HTTP route in mocked transport')

class KieTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.image_path=cls.root/'fixture.png';cls.video_path=cls.root/'fixture.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=256x256','-frames:v','1',str(cls.image_path)],check=True)
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=720x1280:r=24','-t','2','-c:v','libx264','-pix_fmt','yuv420p',str(cls.video_path)],check=True)
        cls.image=cls.image_path.read_bytes();cls.video=cls.video_path.read_bytes()
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('requests.sessions.Session.request',side_effect=AssertionError('LIVE HTTP FORBIDDEN IN LOCAL TESTS')))
        self.stack.enter_context(patch.dict(os.environ,{'CLIPFORGE_KIE_ALLOW_PAID_REQUESTS':'1','CLIPFORGE_VIDEO_PROVIDER_API_KEY':KEY}))
        self.stack.enter_context(patch.object(kie,'public_dns',side_effect=lambda url:kie.safe_url(url)))
    def client(self,transport):return kie.KieClient(KEY,session=transport,sleep=lambda _:None)

    def test_01_paid_gate_blocks_all_calls(self):
        t=Transport(self.image);c=self.client(t)
        with patch.dict(os.environ,{'CLIPFORGE_KIE_ALLOW_PAID_REQUESTS':'0'}):
            self.assertFalse(kie.KieVideoProvider(c).status()['available'])
            with self.assertRaises(kie.KieError):c.generate_image('test',smoke=True)
            with self.assertRaises(kie.KieError):c.upload(self.image_path)
        self.assertEqual(t.calls,[])

    def test_02_image_upload_create_poll_download_without_key_leak(self):
        t=Transport(self.image);c=self.client(t)
        raw=c.generate_image('Keep the actual reference subject consistent.',references=[self.image_path])
        self.assertEqual(raw,self.image);self.assertEqual(t.task_count,1)
        create=next(call for call in t.calls if call[1].endswith('/createTask'))
        self.assertEqual(create[2]['json']['model'],'google/nano-banana-edit')
        self.assertEqual(create[2]['json']['input']['aspect_ratio'],'16:9')
        self.assertEqual(create[2]['json']['input']['image_urls'],[CDN+'/reference.png'])
        self.assertNotIn(KEY,json.dumps(create[2]['json']))
        for method,url,kwargs in t.calls:
            self.assertFalse(kwargs['allow_redirects'])
            if url.startswith(CDN):self.assertEqual(kwargs['headers'],{})
            else:self.assertEqual(kwargs['headers']['Authorization'],'Bearer '+KEY)

    def test_03_video_official_payload_and_reference_preparation(self):
        t=Transport(self.image,self.video);c=self.client(t)
        body=image_video.Generation(image_id='a'*32,prompt='Slowly move the camera',duration=2,ratio='9:16',motion='Camera pan')
        self.assertEqual(kie.KieVideoProvider(c).generate(self.image_path,body),self.video)
        payload=next(x[2]['json'] for x in t.calls if x[1].endswith('/createTask'))
        self.assertEqual(payload['model'],kie.VIDEO_MODEL)
        self.assertEqual(payload['input']['duration'],2)
        self.assertEqual(payload['input']['resolution'],'720p')
        self.assertIn('first_frame_url',payload['input']);self.assertNotIn('aspect_ratio',payload['input'])
        self.assertEqual(kie.KieVideoProvider(c).status()['durations'],list(range(2,16)))

    def test_04_safe_errors_and_no_paid_retry(self):
        class Failure:
            calls=0
            def request(self,*a,**kw):self.calls+=1;return Response(dict(code=402,msg=KEY,data={}))
        t=Failure();c=self.client(t)
        with self.assertRaises(kie.KieError) as e:c.task(kie.SMOKE_MODEL,{'prompt':'test'})
        self.assertNotIn(KEY,str(e.exception));self.assertEqual(t.calls,1)
        class ConnectionFailure:
            def request(self,*a,**kw):raise RuntimeError(KEY)
        with self.assertRaises(kie.KieError) as e:self.client(ConnectionFailure()).task(kie.SMOKE_MODEL,{})
        self.assertNotIn(KEY,str(e.exception))

    def test_05_poll_timeout_does_not_resubmit(self):
        class Time:
            value=0
            def clock(self):return self.value
            def sleep(self,n):self.value+=n
        class Waiting(Transport):
            def request(self,method,url,**kwargs):
                if url.endswith('/recordInfo'):return Response(dict(code=200,data={'state':'generating'}))
                return super().request(method,url,**kwargs)
        timer=Time();t=Waiting(self.image);c=kie.KieClient(KEY,{'timeout_seconds':10},session=t,clock=timer.clock,sleep=timer.sleep)
        with self.assertRaisesRegex(kie.KieError,'may still finish'):c.task(kie.SMOKE_MODEL,{})
        self.assertEqual(t.task_count,1)

    def test_06_download_ssrf_redirect_invalid_media_and_size(self):
        for url in ['http://tempfile.aiquickdraw.com/x','https://localhost/x','https://127.0.0.1/x','https://api.kie.ai@evil.example/x','https://evil.example/x']:
            with self.assertRaises(kie.KieError):kie.safe_url(url)
        class Redirect:
            def request(self,*a,**kw):return Response(b'',302,{'Location':'https://evil.example/result'})
        with self.assertRaises(kie.KieError):self.client(Redirect()).download(CDN+'/x','image')
        with self.assertRaises(kie.KieError):self.client(Transport(b'not an image')).download(CDN+'/image.png','image')
        with self.assertRaises(kie.KieError):self.client(Transport(self.image))._read(Response(b'1234'),3)

    def test_07_public_dns_rejects_private_addresses(self):
        # Call the original helper despite the suite's no-network DNS replacement.
        with patch.object(kie.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(kie.KieError):PUBLIC_DNS(CDN+'/x')

    def test_08_factories_use_existing_env_without_network(self):
        with patch.dict(os.environ,{'CLIPFORGE_KIE_ALLOW_PAID_REQUESTS':'0','CLIPFORGE_VIDEO_PROVIDER_FACTORY':'','CLIPFORGE_IMAGE_PROVIDER_FACTORY':''}):
            for kind in ('video','image'):
                p=load(image_video.Unavailable(),kind=kind)
                self.assertTrue(p.status()['configured']);self.assertFalse(p.status()['available'])
                self.assertNotIn(KEY,json.dumps(p.status()))

    def test_09_low_cost_smoke_is_dry_by_default(self):
        out=StringIO()
        with patch('sys.stdout',out):self.assertEqual(smoke_main([]),0)
        data=json.loads(out.getvalue());self.assertEqual(data['network_requests'],0)
        self.assertEqual(data['model'],kie.SMOKE_MODEL);self.assertEqual(data['maximum_generation_submissions'],1)
        with patch.dict(os.environ,{'CLIPFORGE_KIE_ALLOW_PAID_REQUESTS':'0'}):
            with self.assertRaises(kie.KieError):smoke_main(['--approve-paid-image-test','--output',str(self.root/'new-output.png')])

    def test_10_async_thumbnail_pipeline_and_duplicate_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);jid=uuid.uuid4().hex;folder=root/jid;folder.mkdir()
            concepts=[]
            for i in range(3):
                (folder/f'reference_{i}.jpg').write_bytes(self.image)
                concepts.append(dict(id=i,image_prompt=f'Contract test concept {i}',title='Test fixture'))
            job=dict(id=jid,status='completed',analysis={'concepts':concepts})
            t=Transport(self.image,states=['success']);provider=kie.KieImageProvider(self.client(t))
            host=FastAPI();thumbnail_ai.register(host)
            with patch.object(thumbnail_ai,'ROOT',root),patch.object(thumbnail_ai,'JOBS',{jid:job}),patch.object(thumbnail_ai,'provider',provider):
                http=TestClient(host)
                self.assertEqual(http.post(f'/thumbnail-ai/jobs/{jid}/generate').status_code,202)
                http.post(f'/thumbnail-ai/jobs/{jid}/generate')
                for _ in range(100):
                    state=http.get(f'/thumbnail-ai/jobs/{jid}').json()
                    if state.get('generation_status') in ('completed','failed'):break
                    time.sleep(.1)
                self.assertEqual(state.get('generation_status'),'completed',state)
                self.assertEqual(len(state['generated']),3);self.assertEqual(t.task_count,3)
                self.assertEqual(http.post(f'/thumbnail-ai/jobs/{jid}/generate').status_code,200)
                self.assertEqual(t.task_count,3)
                for image in state['generated']:self.assertTrue(http.get(image['url']).content.startswith(b'\xff\xd8'))
                self.assertNotIn(KEY,json.dumps(state))

    def test_11_video_fixture_normalizes_and_imports_into_editor(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);assets=root/'assets';jobs=root/'jobs';assets.mkdir();jobs.mkdir()
            t=Transport(self.image,self.video,states=['success']);provider=kie.KieVideoProvider(self.client(t))
            with patch.object(editor,'ASSETS',assets),patch.object(editor,'JOBS',jobs),patch.object(image_video,'STATE',{}),patch.object(image_video,'provider',provider):
                a=editor.save_asset(self.image_path,'LOCAL TEST FIXTURE, NOT AI OUTPUT')
                host=FastAPI();editor.register(host);image_video.register(host);http=TestClient(host)
                r=http.post('/image-video/jobs',json=dict(image_id=a['id'],duration=2,ratio='9:16',motion='Natural movement'))
                self.assertEqual(r.status_code,202,r.text);jid=r.json()['id']
                for _ in range(150):
                    state=http.get('/image-video/jobs/'+jid).json()
                    if state['status'] in ('completed','failed'):break
                    time.sleep(.1)
                self.assertEqual(state['status'],'completed',state)
                self.assertEqual(http.post('/editor/import',json={'url':state['url']}).status_code,200)
                self.assertIn(b'ftyp',http.get(state['url']).content[:32])
                self.assertEqual(t.task_count,1);self.assertNotIn(KEY,json.dumps(state))

if __name__=='__main__':unittest.main(verbosity=2)
