"""Server-only adapter configuration. No guessed vendor, URL or API protocol."""
import importlib
import json
import os
import re

def load(fallback,kind='video'):
    variable='CLIPFORGE_IMAGE_PROVIDER_FACTORY' if kind=='image' else 'CLIPFORGE_VIDEO_PROVIDER_FACTORY'
    spec=os.environ.get(variable,'').strip()
    if not spec and os.environ.get('CLIPFORGE_VIDEO_PROVIDER_API_KEY','').strip():
        spec='clipforge_studios.backend.kie_provider:build_'+kind+'_provider'
    if not spec:return fallback
    try:
        if not re.fullmatch(r'[a-zA-Z_][\w.]*(?::[a-zA-Z_]\w*)',spec):raise ValueError()
        module,name=spec.split(':',1)
        config=json.loads(os.environ.get('CLIPFORGE_VIDEO_PROVIDER_CONFIG','{}'))
        if not isinstance(config,dict):raise ValueError()
        provider=getattr(importlib.import_module(module),name)(
            api_key=os.environ.get('CLIPFORGE_VIDEO_PROVIDER_API_KEY',''),config=config)
        if not callable(provider.generate) or not callable(provider.status):raise ValueError()
        status=provider.status()
        if not isinstance(status,dict) or not isinstance(status.get('available'),bool):raise ValueError()
        if status['available'] and kind=='video':
            if not status.get('name') or not status.get('durations') or not status.get('ratios'):raise ValueError()
            if any(type(t)!=int or not 2<=t<=30 for t in status['durations']):raise ValueError()
            if any(r not in ('16:9','9:16','1:1') for r in status['ratios']):raise ValueError()
        return provider
    except Exception:
        # Factory/network exceptions may contain credentials. Never serialize them.
        class Invalid:
            def status(self):return dict(available=False,name=None,durations=[],ratios=[],reason='Video provider configuration is invalid or its adapter is unavailable. Check server-side adapter configuration; no credentials are exposed.')
            def generate(self,*args):raise RuntimeError('Video provider is unavailable')
        return Invalid()
