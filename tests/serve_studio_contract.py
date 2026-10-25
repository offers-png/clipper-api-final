"""LOOPBACK-ONLY browser fixture. Never a production entry point or real auth.

No production credentials/services. All users/plans below are explicit test data.
"""
import base64
import json
import os
from pathlib import Path
import time
from fastapi import FastAPI,Request,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from clipforge_studios.access import Access
from clipforge_studios.install import install
from clipforge_studios.backend.resource_paths import DATA

USER={'id':'10000000-0000-4000-8000-000000000001','aud':'authenticated','role':'authenticated','email':'studio-test@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z','created_at':'2026-01-01T00:00:00Z','app_metadata':{'provider':'email'},'user_metadata':{}}
OTHER={**USER,'id':'10000000-0000-4000-8000-000000000002','email':'other-test@example.invalid'}
def token(user):
 encode=lambda value:base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
 return encode({'alg':'HS256','typ':'JWT'})+'.'+encode({'sub':user['id'],'email':user['email'],'aud':'authenticated','exp':int(time.time())+3600})+'.local-contract-signature'
TOKEN=token(USER);OTHER_TOKEN=token(OTHER)
state={'plan':'active','allowed':True,'usage':[]}
def authenticate(value):
 if value==TOKEN:return {'id':USER['id'],'email':USER['email']}
 if value==OTHER_TOKEN:return {'id':OTHER['id'],'email':OTHER['email']}
 raise HTTPException(401,'Invalid local contract session')
def access(email):return dict(allowed=state['allowed'],plan=state['plan'],reason='contract-fixture',clips_remaining=3 if state['plan']=='trial' else None)
app=FastAPI()
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5174'],allow_headers=['*'],allow_methods=['*'])
@app.middleware('http')
async def loopback(request,call_next):
 if not request.client or request.client.host not in ('127.0.0.1','::1','testclient'):return JSONResponse({'detail':'Local contract fixture only'},403)
 return await call_next(request)
@app.post('/auth/v1/token')
async def sign_in(request:Request):
 body=await request.json()
 if body.get('email')!=USER['email'] or body.get('password')!='local-contract-only':raise HTTPException(401,'Use local test credentials only')
 return dict(access_token=TOKEN,refresh_token='local-contract-refresh',token_type='bearer',expires_in=3600,expires_at=int(time.time())+3600,user=USER)
@app.get('/auth/v1/user')
def user(request:Request):
 actor=authenticate(request.headers.get('authorization','')[7:]);return USER if actor['id']==USER['id'] else OTHER
@app.post('/auth/v1/logout')
def logout():return {}
@app.post('/billing/check-access')
def billing():return access(USER['email'])
@app.post('/__test/plan')
async def plan(request:Request):state.update(await request.json());return {'ok':True}
@app.get('/__test/status')
def test_status():return {'usage':len(state['usage']),'plan':state['plan']}
os.environ['CLIPFORGE_ENABLE_STUDIOS']='1'
os.environ['CLIPFORGE_KIE_ALLOW_PAID_REQUESTS']='0'
install(app,local_only=False,access=Access(DATA/'browser-contract.sqlite3',authenticate,access,state['usage'].append,limit=lambda:3))
