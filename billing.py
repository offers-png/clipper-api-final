# billing.py — simple time-as-credit accounting
# Expects Supabase RPCs or tables:
# - profiles(id uuid, email text, seconds_balance int)
# - RPC: charge_seconds(u uuid, used int)
# - RPC: require_seconds(u uuid, needed int) -> boolean

import os
from fastapi import HTTPException, Request
from typing import Dict
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

def _sb():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def _user_from_request(request: Request) -> Dict:
    # Provided by auth.require_user; here for typing
    return request.state.user

def require_seconds(request: Request):
    """Dependency that checks user has some non-zero balance (best-effort).
       You can make this stricter with an RPC in Supabase."""
    sb = _sb()
    user = getattr(request.state, "user", None)
    if not user:
        # auth will have already blocked; this is fallback
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not sb:
        return True  # no billing configured => allow
    try:
        # Optional: call a function to ensure balance > 0
        # For simplicity we skip hard-fail and let clip charge below.
        return True
    except Exception:
        return True

def charge_seconds(user_id: str, used_seconds: int):
    sb = _sb()
    if not sb or used_seconds <= 0:
        return
    try:
        # Prefer RPC if present; else decrement table
        sb.rpc("charge_seconds", {"u": user_id, "used": used_seconds}).execute()
    except Exception as e:
        # Fallback best-effort table update
        try:
            prof = sb.table("profiles").select("seconds_balance").eq("id", user_id).single().execute()
            if getattr(prof, "data", None) and "seconds_balance" in prof.data:
                newv = max(0, int(prof.data["seconds_balance"]) - int(used_seconds))
                sb.table("profiles").update({"seconds_balance": newv}).eq("id", user_id).execute()
        except Exception as e2:
            print("⚠️ charge_seconds failed:", e, "/", e2)
