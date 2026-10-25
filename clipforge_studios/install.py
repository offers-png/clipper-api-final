"""Opt-in installation. Existing deployments and routes are unchanged by default."""
import os
from fastapi import FastAPI
from starlette.responses import JSONResponse

def install(host:FastAPI):
    if os.environ.get('CLIPFORGE_ENABLE_STUDIOS')!='1':return False
    from .app import app
    # This branch is a local development integration, not a new public auth bypass.
    @app.middleware('http')
    async def local_only(request,call_next):
        if request.client is None or request.client.host not in ('127.0.0.1','::1','localhost','testclient'):
            return JSONResponse({'detail':'Studio API is restricted to local development until production authorization is reviewed.'},status_code=403)
        return await call_next(request)
    host.mount('/studios-api',app)
    return True
