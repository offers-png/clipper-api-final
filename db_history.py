# db_history.py
# Database-only logic. NO FastAPI. NO ffmpeg. NO whisper.

import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

from typing import Optional

_sb: Optional[Client] = None

from typing import Optional

def get_db() -> Optional[Client]:

    """Lazy init. Never crash the app if env vars are missing."""
    global _sb
    if _sb:
        return _sb
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    _sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _sb


def insert_transcript(
    *,
    user_id: str,
    source_name: str,
    transcript: str,
    duration: float | None = None,
):
    """Safe insert. Errors are swallowed by caller."""
    db = get_db()
    if not db:
        return False

    db.table("history").insert({
        "user_id": user_id,
        "job_type": "transcript",
        "source_name": source_name,
        "transcript": transcript,
        "duration": duration,
    }).execute()
    return True


def get_user_history(user_id: str, limit: int = 50):
    db = get_db()
    if not db:
        return []

    res = (
        db.table("history")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []
