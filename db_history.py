# db_history.py
# Database-only logic. NO FastAPI. NO ffmpeg. NO whisper.

print("🔥 LOADED db_history.py FROM:", __file__)
import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ROLE")  # service_role key

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

from typing import Optional
from datetime import datetime, timezone

def insert_transcript(
    *,
    user_id: str,
    source_name: str,
    transcript: str,
    duration: Optional[float] = None,
):
    """Safe insert. Errors are logged."""
    db = get_db()
    if not db:
        print("NO DB CLIENT")
        return False

    res = db.table("history").insert({
        "user_id": user_id,
        "job_type": "transcript",
        "source_name": source_name,
        "transcript": transcript,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    print("🔥 insert_transcript CALLED", user_id, source_name)

    print("SUPABASE INSERT RESULT:", res)

    if res.data:
        return True

    print("SUPABASE INSERT ERROR:", res.error)
    return False



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
