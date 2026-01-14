# db_history.py
# Database-only logic. NO FastAPI. NO ffmpeg. NO whisper.
print("🔥 LOADED db_history.py FROM:", __file__)

import os
from typing import Optional
from datetime import datetime, timezone
from supabase import create_client, Client

# Environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Global client instance
_sb: Optional[Client] = None


def get_db() -> Optional[Client]:
    """Lazy init. Never crash the app if env vars are missing."""
    global _sb
    if _sb:
        return _sb
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️ Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        return None
    try:
        _sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase client initialized")
        return _sb
    except Exception as e:
        print(f"❌ Failed to create Supabase client: {e}")
        return None

def insert_transcript(
    *,
    user_id: str,
    source_name: str,
    transcript: str,
    titles: list | None = None,
    hooks: list | None = None,
    hashtags: list | None = None,
    duration: float | None = None,
    preview_url: str | None = None,
    final_url: str | None = None,
) -> bool:
    db = get_db()
    if not db:
        return False

    data = {
        "user_id": user_id,
        "job_type": "transcript",
        "source_name": source_name,
        "transcript": transcript,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if titles is not None:
        data["titles"] = titles
    if hooks is not None:
        data["hooks"] = hooks
    if hashtags is not None:
        data["hashtags"] = hashtags
    if duration is not None:
        data["duration"] = duration
    if preview_url:
        data["preview_url"] = preview_url
    if final_url:
        data["final_url"] = final_url

    res = db.table("history").insert(data).execute()
    return bool(res.data)
        
        
       
