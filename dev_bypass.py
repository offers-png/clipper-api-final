"""Local-development sign-in bypass with a fake test user.

Active only when every condition holds:
- CLIPFORGE_DEV_BYPASS=1 in the environment or in this folder's .env file
  (git-ignored; never set it in Render settings);
- the server is not running on Render (Render sets RENDER/RENDER_SERVICE_ID);
- the TCP client is loopback and no proxy forwarding headers are present;
- the Host header is localhost, 127.0.0.1 or ::1 (blocks DNS rebinding);
- the request sends Authorization: Bearer clipforge-dev-bypass. Browsers must
  preflight that header, and CORS only allows the local frontend, so other
  websites cannot make a visitor's browser use the bypass.
"""
import ipaddress
import os
from pathlib import Path

TOKEN = 'clipforge-dev-bypass'
DEV_USER = dict(id='00000000-0000-4000-8000-00000000de70', email='dev@localhost.test',
                email_verified=True, dev_bypass=True)
LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')
FORWARDING_HEADERS = ('x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'forwarded', 'x-real-ip')


def setting(env_file=Path(__file__).with_name('.env')):
    value = os.getenv('CLIPFORGE_DEV_BYPASS')
    if value is None:
        try:
            for line in env_file.read_text(encoding='utf-8').splitlines():
                key, _, raw = line.partition('=')
                if key.strip() == 'CLIPFORGE_DEV_BYPASS':
                    value = raw.strip().strip('"\'')
        except OSError:
            pass
    return (value or '').strip()


def hosted():
    return any(os.getenv(name) for name in ('RENDER', 'RENDER_SERVICE_ID', 'RENDER_EXTERNAL_URL'))


def dev_actor(request):
    """The fake test user for an explicit local development request, otherwise None."""
    if setting() != '1' or hosted():
        return None
    headers = request.headers
    if headers.get('authorization', '') != 'Bearer ' + TOKEN:
        return None
    if any(name in headers for name in FORWARDING_HEADERS):
        return None
    host = headers.get('host', '')
    host = host[1:host.find(']')] if host.startswith('[') else host.rsplit(':', 1)[0]
    if host.lower() not in LOCAL_HOSTS:
        return None
    try:
        if not ipaddress.ip_address(request.client.host).is_loopback:
            return None
    except (AttributeError, TypeError, ValueError):
        return None
    return dict(DEV_USER)
