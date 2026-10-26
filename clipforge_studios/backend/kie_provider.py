"""KIE Market adapters. Official schemas are linked in KIE_INTEGRATION.md.

No automatic paid retries, no frontend key, and no authenticated startup requests.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time
from urllib.parse import urlsplit
import uuid
import requests
from . import kie_budget
from . import kie_account
from decimal import Decimal,InvalidOperation

API='https://api.kie.ai/api/v1/jobs'
UPLOAD='https://kieai.redpandaai.co/api/file-stream-upload'
DOMAINS=('kie.ai','redpandaai.co','aiquickdraw.com')
VIDEO_MODEL='wan/2-7-image-to-video'
IMAGE_MODEL='google/nano-banana-edit'
SMOKE_MODEL='google/imagen4-fast'

class KieError(RuntimeError):pass

def safe_url(url):
    if not isinstance(url,str) or len(url)>4096:raise KieError('Invalid KIE media URL')
    parsed=urlsplit(url)
    try:port=parsed.port
    except ValueError:raise KieError('Invalid KIE media URL') from None
    host=(parsed.hostname or '').lower()
    if parsed.scheme!='https' or parsed.username or parsed.password or port not in (None,443) or not any(host==d or host.endswith('.'+d) for d in DOMAINS):
        raise KieError('KIE returned an unapproved media host')
    return url

def public_dns(url):
    host=urlsplit(safe_url(url)).hostname
    try:
        ips=[ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)]
        if not ips or not all(ip.is_global for ip in ips):raise ValueError()
    except Exception:raise KieError('KIE media host did not resolve to public addresses') from None

class KieClient:
    def __init__(self,api_key='',config=None,session=None,clock=None,sleep=None):
        self._key=(api_key or '').strip()
        config=config or {}
        self.timeout=int(config.get('timeout_seconds',900))
        if not 10<=self.timeout<=1800:raise ValueError('KIE timeout must be 10–1800 seconds')
        self.session=session or requests.Session()
        self.session.trust_env=False  # No implicit .netrc credentials or proxy auth.
        self.clock=clock or time.monotonic;self.sleep=sleep or time.sleep

    def available(self):
        if kie_account.active():return kie_account.status(bool(self._key))['available']
        if kie_budget.scoped():return bool(self._key) and kie_budget.available()
        return bool(self._key) and os.getenv('CLIPFORGE_KIE_ALLOW_PAID_REQUESTS')=='1'

    def require_approval(self):
        if not self._key:raise KieError('KIE API key is not configured on the server')
        if kie_account.active():
            kie_account.require_execution()
            return
        if kie_budget.scoped():
            kie_budget.require_ticket()
            return
        if not self.available():raise KieError('KIE paid requests are disabled pending explicit approval')

    def _read(self,response,limit):
        chunks=[];size=0
        for chunk in response.iter_content(65536):
            size+=len(chunk)
            if size>limit:raise KieError('KIE response exceeds the local size limit')
            chunks.append(chunk)
        return b''.join(chunks)

    def _json(self,method,url,**kwargs):
        self.require_approval()
        creating=method=='POST' and url==API+'/createTask'
        if creating and kie_account.active():
            payload=kwargs.get('json',{})
            kie_account.claim_submission(payload.get('model'),payload.get('input',{}),self.credit_balance())
        try:
            with self.session.request(method,url,headers={'Authorization':'Bearer '+self._key},
                    timeout=(10,60),allow_redirects=False,stream=True,**kwargs) as response:
                if response.status_code!=200:raise KieError('KIE HTTP request failed; no automatic resubmission was attempted')
                body=json.loads(self._read(response,1024*1024))
            if not isinstance(body,dict) or body.get('code')!=200:
                if creating and kie_account.active() and isinstance(body,dict) and body.get('code') in (400,401,402,403,422):kie_account.terminal('rejected')
                # Never expose msg/failMsg/request headers: providers may echo inputs.
                raise KieError('KIE rejected the request; check account credits and server configuration')
            if not isinstance(body.get('data'),dict):raise KieError('KIE response is missing task data')
            return body['data']
        except KieError:raise
        except Exception:raise KieError('KIE connection or response failed; no automatic resubmission was attempted') from None

    def credit_balance(self):
        """Read-only official balance request; never creates a generation task."""
        if not self._key:raise KieError('KIE is not configured on the server')
        try:
            with self.session.request('GET','https://api.kie.ai/api/v1/chat/credit',
                    headers={'Authorization':'Bearer '+self._key},timeout=(10,30),allow_redirects=False,stream=True) as response:
                if response.status_code!=200:raise ValueError()
                body=json.loads(self._read(response,16384))
            if body.get('code')!=200 or isinstance(body.get('data'),bool):raise ValueError()
            value=Decimal(str(body['data']))
            if not value.is_finite() or value<0:raise ValueError()
            return value
        except Exception:raise KieError('KIE credit balance could not be verified; no paid request was submitted') from None

    def upload(self,path):
        self.require_approval()
        path=Path(path)
        if not path.is_file() or not 0<path.stat().st_size<=10_000_000:raise KieError('KIE reference image must be at most 10 MB')
        raw=path.read_bytes()[:12]
        ext,mime=('png','image/png') if raw.startswith(b'\x89PNG') else ('jpg','image/jpeg') if raw.startswith(b'\xff\xd8') else ('webp','image/webp') if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP' else (None,None)
        if not ext:raise KieError('KIE reference must be PNG, JPEG or WebP')
        with path.open('rb') as stream:
            result=self._json('POST',UPLOAD,files={'file':('reference.'+ext,stream,mime)},data={'uploadPath':'clipforge/references','fileName':uuid.uuid4().hex+'.'+ext})
        return safe_url(result.get('downloadUrl'))

    def task(self,model,inputs):
        if kie_budget.scoped() and not kie_account.active():kie_budget.claim_submission(model,inputs)
        created=self._json('POST',API+'/createTask',json={'model':model,'input':inputs})
        task_id=created.get('taskId','')
        if not isinstance(task_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}',task_id):raise KieError('KIE returned an invalid task ID')
        if kie_account.active():kie_account.record_task(task_id)
        elif kie_budget.scoped():kie_budget.record_task(task_id)
        deadline=self.clock()+self.timeout;interval=2
        while self.clock()<deadline:
            data=self._json('GET',API+'/recordInfo',params={'taskId':task_id})
            state=data.get('state')
            if state=='success':
                if kie_account.active():kie_account.terminal('success')
                try:urls=json.loads(data['resultJson'])['resultUrls']
                except Exception:raise KieError('KIE completed without valid media results') from None
                if not isinstance(urls,list) or not urls:raise KieError('KIE completed without media results')
                return safe_url(urls[0])
            if state=='fail':
                if kie_account.active():kie_account.terminal('failed')
                raise KieError('KIE generation failed; no result or replacement task was created')
            if state not in ('waiting','queuing','generating'):raise KieError('KIE returned an unknown task state')
            self.sleep(min(interval,max(0,deadline-self.clock())));interval=min(20,interval*1.5)
        raise KieError('KIE task timed out; it may still finish remotely. No replacement task was created; check the KIE dashboard before retrying')

    def download(self,url,kind):
        self.require_approval()
        # Do not forward the API Authorization header to any result/CDN URL.
        for _ in range(4):
            public_dns(url)
            try:
                with self.session.request('GET',url,headers={},timeout=(10,90),allow_redirects=False,stream=True) as response:
                    if response.status_code in (301,302,303,307,308):url=safe_url(response.headers.get('Location'));continue
                    if response.status_code!=200:raise KieError('KIE result download failed')
                    raw=self._read(response,20_000_000 if kind=='image' else 512*1024**2)
            except KieError:raise
            except Exception:raise KieError('KIE result download failed') from None
            if kind=='image' and not (raw.startswith(b'\x89PNG') or raw.startswith(b'\xff\xd8') or (raw[:4]==b'RIFF' and raw[8:12]==b'WEBP')):raise KieError('KIE returned invalid image bytes')
            if kind=='video' and b'ftyp' not in raw[:32]:raise KieError('KIE returned invalid MP4 bytes')
            return raw
        raise KieError('KIE media redirect limit exceeded')

    def generate_image(self,prompt,ratio='16:9',references=(),smoke=False):
        self.require_approval()
        if ratio not in ('16:9','9:16','1:1'):raise KieError('Unsupported KIE image aspect ratio')
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>5000:raise KieError('KIE image prompt must contain 1–5000 characters')
        if smoke:
            if references:raise KieError('The single-image smoke test does not upload references')
            model=SMOKE_MODEL;inputs={'prompt':prompt,'aspect_ratio':ratio}
        else:
            if not 1<=len(references)<=10:raise KieError('Reference-based thumbnails need 1–10 actual source images')
            model=IMAGE_MODEL;inputs={'prompt':prompt,'image_urls':[self.upload(p) for p in references],'aspect_ratio':ratio,'output_format':'png'}
        return self.download(self.task(model,inputs),'image')

def reason(client):
    if kie_account.active():return kie_account.status(bool(client._key))['reason']
    if kie_budget.scoped():
        return 'Account-only KIE test: one 5-second 720p video, 80 credits (approximately $0.40), maximum approved budget $1. No automatic retries.' if client.available() else 'KIE is restricted to one approved account and one video test. This account is not enabled or the test attempt has already been used.'
    return 'KIE AI is configured for generation. Each generation sends reference media to KIE and uses paid credits; remote authentication is verified when a request runs.' if client.available() else 'KIE API key is not configured on the server' if not client._key else 'KIE AI is configured using the existing server key. Paid generation is disabled pending approval; no additional API key is needed.'

class KieVideoProvider:
    def __init__(self,client):self.client=client
    def status(self):
        status=dict(available=self.client.available(),configured=bool(self.client._key),name='KIE AI · Wan 2.7',model=VIDEO_MODEL,reason=reason(self.client),durations=[5] if kie_budget.scoped() and not kie_account.active() else list(range(2,16)),ratios=['16:9','9:16','1:1'],paid=True)
        if kie_account.active():status.update(kie_account.status(bool(self.client._key)))
        return status
    def generate(self,image_path,request):
        if kie_budget.scoped() and not kie_account.active():
            with kie_budget.attempt(request):return self._generate(image_path,request)
        return self._generate(image_path,request)
    def _generate(self,image_path,request):
        self.client.require_approval()
        if request.duration not in range(2,16) or request.ratio not in ('16:9','9:16','1:1'):raise KieError('Wan 2.7 supports 2–15 seconds and the prepared reference aspect ratios')
        styles={'Cinematic':'Cinematic camera movement.','Natural movement':'Subtle natural movement.','Camera zoom':'Slow camera zoom.','Camera pan':'Slow camera pan.','Animated character':'Animate the reference character consistently.','Custom prompt':''}
        prompt=((request.prompt or 'Bring the actual reference image to life with subtle movement.')+' '+styles[request.motion]).strip()
        # Wan uses first_frame_url, not an undocumented aspect_ratio parameter.
        # Fit/pad the reference first; final output ratio is checked by image_video.
        from . import editor
        w,h=editor.SIZES[request.ratio]
        with tempfile.TemporaryDirectory(prefix='kie-reference-') as temp:
            image=Path(temp)/'reference.png'
            editor.run(['-i',str(Path(image_path).resolve()),'-frames:v','1','-vf',f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2',str(image)],temp)
            url=self.client.upload(image)
        inputs={'prompt':prompt,'first_frame_url':url,'duration':request.duration,'resolution':'720p','prompt_extend':False}
        return self.client.download(self.client.task(VIDEO_MODEL,inputs),'video')

class KieImageProvider:
    def __init__(self,client):self.client=client
    def status(self):
        status=dict(available=False if kie_budget.scoped() and not kie_account.active() else self.client.available(),configured=bool(self.client._key),name='KIE AI · Nano Banana Edit',model=IMAGE_MODEL,reason='Image generation is excluded from the approved video-only test.' if kie_budget.scoped() and not kie_account.active() else reason(self.client),paid=True)
        if kie_account.active():status.update(kie_account.status(bool(self.client._key)))
        return status
    def generate(self,concept,references):
        if kie_budget.scoped() and not kie_account.active():raise KieError('Image generation is excluded from the approved video-only test')
        prompt=('Create a professional thumbnail artwork based on the actual reference image and this proposed creative brief. '
            'Keep the reference subject consistent. Do not invent factual events. Leave space for editable headline text; do not bake a title into the image. '+str(concept.get('image_prompt','')))
        return self.client.generate_image(prompt,references=references)

def build_video_provider(*,api_key='',config=None):return KieVideoProvider(KieClient(api_key,config))
def build_image_provider(*,api_key='',config=None):return KieImageProvider(KieClient(api_key,config))
