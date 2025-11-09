# auth.py — minimal Supabase auth (JWT from frontend) or dev fallback

import os, base64, json
from fastapi import Request, HTTPException

SUPABASE_JWT_HEADER = "authorization"  # "Bearer <jwt>" expected
DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000000")
DEV_USER_EMAIL = os.getenv("DEV_USER_EMAIL", "dev@clipforge.app")

def _decode_jwt_noverify(jwt: str):
    # Only to grab uid/email without verification (Supabase will still authorize DB ops by RLS)
    try:
        parts = jwt.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1] + "==="
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
        return data
    except Exception:
        return None

async def require_user(request: Request):
    auth = request.headers.get(SUPABASE_JWT_HEADER, "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ",1)[1].strip()
        data = _decode_jwt_noverify(token) or {}
        uid = data.get("sub") or data.get("user_id") or DEV_USER_ID
        email = (data.get("email") or DEV_USER_EMAIL)
        user = {"id": uid, "email": email}
        request.state.user = user
        return user

    # Dev mode: allow unauthenticated requests if DEV_ALLOW=1
    if os.getenv("DEV_ALLOW","0") == "1":
        user = {"id": DEV_USER_ID, "email": DEV_USER_EMAIL}
        request.state.user = user
        return user

    raise HTTPException(status_code=401, detail="Unauthorized")
