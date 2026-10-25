"""Additive Studio access checks; existing auth/billing modules are unchanged."""
import asyncio
import contextvars
from contextlib import contextmanager
from datetime import date
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit
import uuid
from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse,Response

operation=contextvars.ContextVar('studio_billing_operation',default=None)

@lru_cache(maxsize=1)
def auth_client():
    from supabase import create_client
    url=os.getenv('SUPABASE_URL','');key=os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_SERVICE_ROLE_KEY','')
    if not url or not key:raise HTTPException(503,'Studio authentication is not configured')
    return create_client(url,key)

def verified_user(token):
    try:
        user=auth_client().auth.get_user(token).user
        actor=dict(id=str(uuid.UUID(str(user.id))),email=user.email)
        if not actor['email']:raise ValueError()
        return actor
    except HTTPException:raise
    except Exception as error:
        if getattr(error,'status',None) in (400,401,403,422):raise HTTPException(401,'Session is invalid or expired') from None
        raise HTTPException(401,'Could not verify the signed-in user') from None

def clip_access(email):
    from stripe_billing import check_clip_access
    return check_clip_access(email)

def record_clip(email):
    from stripe_billing import record_clip_used
    record_clip_used(email)

def trial_limit():
    from stripe_billing import FREE_CLIPS_PER_DAY
    return FREE_CLIPS_PER_DAY

def canonical(path):
    path=urlsplit(path).path
    return path[len('/studios-api'):] if path.startswith('/studios-api/') else path

def resource_key(path):
    path=canonical(path)
    match=re.match(r'^/(?:studio/(?:jobs|files)|editor/(?:jobs|assets|files)|image-video/jobs|thumbnail-ai/jobs|api/videos|local-original/batches)/([a-f0-9]{32})(?:/|$)',path)
    if match:return 'entity:'+match[1]
    if path.startswith(('/media/','/api/clips/')):return 'url:'+path
    return None

def metered(path):
    return path in ('/studio/jobs','/editor/jobs','/image-video/jobs','/local-original/manual') or bool(re.fullmatch(r'/api/videos/[a-f0-9]{32}/selection|/thumbnail-ai/jobs/[a-f0-9]{32}/generate',path))

class Access:
    def __init__(self,path,authenticate=verified_user,access=clip_access,record=record_clip,limit=trial_limit):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.authenticate=authenticate;self.access=access;self.record=record;self.limit=limit
        self.lock=threading.RLock()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS owners (key TEXT PRIMARY KEY, user TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS usage (id TEXT PRIMARY KEY,user TEXT,day TEXT,status TEXT,created REAL)')
    @contextmanager
    def db(self):
        connection=sqlite3.connect(self.path,timeout=30)
        try:
            with connection:yield connection
        finally:connection.close()
    def owns(self,key,user):
        with self.db() as db:return db.execute('SELECT 1 FROM owners WHERE key=? AND user=?',(key,user)).fetchone() is not None
    def claim(self,key,user):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO owners VALUES (?,?)',(key,user))
            if not db.execute('SELECT 1 FROM owners WHERE key=? AND user=?',(key,user)).fetchone():raise HTTPException(404,'Studio resource not found')
    def check_body(self,value,user):
        if isinstance(value,list):
            for item in value:self.check_body(item,user)
        elif isinstance(value,dict):
            for key,item in value.items():
                if key in ('source_id','asset_id','image_id','video_id') and isinstance(item,str) and not self.owns('entity:'+item,user):raise HTTPException(404,'Studio resource not found')
                if key=='url' and isinstance(item,str):
                    check=resource_key(item) or 'url:'+canonical(item)
                    if not self.owns(check,user):raise HTTPException(404,'Studio resource not found')
                self.check_body(item,user)
    def claim_json(self,value,user):
        if isinstance(value,list):
            for item in value:self.claim_json(item,user)
        elif isinstance(value,dict):
            for key,item in value.items():
                if key in ('id','job_id','video_id','asset_id') and isinstance(item,str) and re.fullmatch('[a-f0-9]{32}',item):self.claim('entity:'+item,user)
                if (key.endswith('_url') or key=='url') and isinstance(item,str) and item.startswith('/'):
                    self.claim('url:'+canonical(item),user)
                self.claim_json(item,user)
    def filter_json(self,path,data,user):
        if path=='/local-original/history':data['records']=[r for r in data.get('records',[]) if self.owns('entity:'+r.get('id',''),user)]
        if path=='/api/library/thumbnails':data['items']=[r for r in data.get('items',[]) if self.owns(resource_key(r.get('url','')) or 'missing',user)]
        return data
    def reserve(self,actor):
        with self.lock:
            try:access=self.access(actor['email'])
            except Exception:raise HTTPException(503,'Clip access could not be verified') from None
            if access.get('reason')=='db_unavailable' or access.get('plan') not in ('active','trial'):raise HTTPException(503 if access.get('reason')=='db_unavailable' else 403,'Clip access is unavailable or an upgrade is required')
            if not access.get('allowed'):raise HTTPException(403,'Upgrade required or daily clip limit reached')
            day=date.today().isoformat();rid=uuid.uuid4().hex
            with self.db() as db:
                db.execute("UPDATE usage SET status='failed' WHERE status='pending' AND created<?",(time.time()-3600,))
                pending=db.execute("SELECT count(*) FROM usage WHERE user=? AND day=? AND status='pending'",(actor['id'],day)).fetchone()[0]
                used=db.execute("SELECT count(*) FROM usage WHERE user=? AND day=? AND status='success'",(actor['id'],day)).fetchone()[0]
                if access['plan']=='trial':
                    # Same one-usage-per-batch semantics as Original /clip_multi.
                    remaining=min(int(access.get('clips_remaining') or 0),max(0,self.limit()-used))
                    if pending>=remaining:raise HTTPException(403,'Daily clip limit reached or processing jobs already reserved')
                db.execute('INSERT INTO usage VALUES (?,?,?,?,?)',(rid,actor['id'],day,'pending',time.time()))
            return dict(manager=self,actor=actor,id=rid,submitted=False)
    def finish(self,op,success):
        with self.lock:
            with self.db() as db:
                changed=db.execute("UPDATE usage SET status=? WHERE id=? AND status='pending'",('success' if success else 'failed',op['id'])).rowcount
            if changed and success:
                try:self.record(op['actor']['email'])
                except Exception:pass  # Local success ledger still prevents trial-quota bypass.

class BillingPool:
    def __init__(self,base):self.base=base
    def submit(self,fn,*args,**kwargs):
        op=operation.get();context=contextvars.copy_context()
        if op:op['submitted']=True
        def run():
            try:return context.run(fn,*args,**kwargs)
            finally:
                if op:
                    job=args[0] if args and isinstance(args[0],dict) else {}
                    success=job.get('generation_status',job.get('status'))=='completed'
                    op['manager'].finish(op,success)
        try:return self.base.submit(run)
        except Exception:
            if op:op['submitted']=False
            raise

def wrap_pools():
    from .backend import studio,editor,image_video,thumbnail_ai
    for module,name in ((studio,'_pool'),(editor,'POOL'),(image_video,'POOL'),(thumbnail_ai,'IMAGE_POOL')):
        pool=getattr(module,name,None)
        if pool is not None and not isinstance(pool,BillingPool):setattr(module,name,BillingPool(pool))

class StudioAccess(BaseHTTPMiddleware):
    def __init__(self,app,access,local_only=False):super().__init__(app);self.access=access;self.local_only=local_only
    async def dispatch(self,request,call_next):
        path=canonical(request.url.path)
        if self.local_only:
            if request.client is None or request.client.host not in ('127.0.0.1','::1','localhost','testclient'):return JSONResponse({'detail':'Local Studio API accepts loopback clients only'},403)
            return await call_next(request)
        if path=='/health':return JSONResponse(dict(ok=True,service='clipforge-studios',authentication_required=True,paid_generation_auto_enabled=False))
        op=None;context=None
        try:
            header=request.headers.get('authorization','')
            if not header.lower().startswith('bearer ') or not 1<len(header)<16384:raise HTTPException(401,'Sign in to use Studio processing')
            if not header[7:].strip():raise HTTPException(401,'Sign in to use Studio processing')
            actor=await asyncio.to_thread(self.access.authenticate,header[7:].strip())
            if not actor or not actor.get('id') or not actor.get('email'):raise HTTPException(401,'Session is invalid or expired')
            key=resource_key(path)
            if key and not self.access.owns(key,actor['id']):raise HTTPException(404,'Studio resource not found')
            if request.method=='POST' and 'application/json' in request.headers.get('content-type',''):
                try:body=await request.json()
                except Exception:raise HTTPException(400,'Invalid JSON body') from None
                self.access.check_body(body,actor['id'])
            if request.method=='POST' and metered(path):
                op=await asyncio.to_thread(self.access.reserve,actor);context=operation.set(op)
            response=await call_next(request)
            if response.status_code in (200,201,202) and 'application/json' in response.headers.get('content-type',''):
                raw=b''.join([chunk async for chunk in response.body_iterator])
                data=json.loads(raw);data=self.access.filter_json(path,data,actor['id']);self.access.claim_json(data,actor['id'])
                headers=dict(response.headers);headers.pop('content-length',None);headers.pop('etag',None)
                response=JSONResponse(data,status_code=response.status_code,headers=headers,background=response.background)
            if op and (not op['submitted'] or response.status_code>=400):
                await asyncio.to_thread(self.access.finish,op,response.status_code<400)
            response.headers['Cache-Control']='private, no-store'
            return response
        except HTTPException as error:
            if op:await asyncio.to_thread(self.access.finish,op,False)
            return JSONResponse({'detail':error.detail},status_code=error.status_code)
        except Exception:
            if op:await asyncio.to_thread(self.access.finish,op,False)
            return JSONResponse({'detail':'Studio access or processing could not be completed safely'},503)
        finally:
            if context is not None:operation.reset(context)
