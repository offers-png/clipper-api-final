"""Opt-in installation. Existing deployments and routes are unchanged by default."""
import os
from fastapi import FastAPI
from starlette.responses import JSONResponse
from .flags import studios_enabled

def install(host:FastAPI,local_only=None,access=None):
    if not studios_enabled():return False
    from .app import app
    from .access import Access,StudioAccess,wrap_pools
    from .backend.resource_paths import DATA
    if local_only is None:local_only=os.getenv('CLIPFORGE_STUDIOS_LOCAL_ONLY')=='1'
    access=access or Access(DATA/'studio-access.sqlite3')
    wrap_pools()
    host.mount('/studios-api',StudioAccess(app,access,local_only=local_only))
    return True
