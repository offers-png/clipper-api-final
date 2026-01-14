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


        
        # Add optional fields if provided
        if preview_url:
            data["preview_url"] = preview_url
        if final_url:
            data["final_url"] = final_url
        if duration is not None:
            data["duration"] = duration
        if titles is not None:
    data["titles"] = titles

if hooks is not None:
    data["hooks"] = hooks

if hashtags is not None:
    data["hashtags"] = hashtags

        print(f"🔥 insert_transcript CALLED - user_id: {user_id}, source: {source_name}")
        
        res = db.table("history").insert(data).execute()
        
        if res.data:
            print(f"✅ SUPABASE INSERT SUCCESS: {len(res.data)} row(s) inserted")
            print(f"   Inserted ID: {res.data[0].get('id') if res.data else 'unknown'}")
            return True
        else:
            print(f"❌ SUPABASE INSERT FAILED - No data returned")
            return False
            
    except Exception as e:
        print(f"❌ SUPABASE INSERT ERROR: {type(e).__name__}: {str(e)}")
        return False


def get_user_history(user_id: str, limit: int = 50) -> list:
    """Retrieve user's transcript history. Returns empty list on error."""
    db = get_db()
    if not db:
        print("❌ NO DB CLIENT - cannot retrieve history")
        return []
    
    try:
        res = (
            db.table("history")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        
        data = res.data or []
        print(f"📚 Retrieved {len(data)} history records for user_id: {user_id}")
        return data
        
    except Exception as e:
        print(f"❌ ERROR retrieving history: {type(e).__name__}: {str(e)}")
        return []


def test_connection() -> bool:
    """Test database connection. Returns True if successful."""
    db = get_db()
    if not db:
        print("❌ DB connection test failed - no client")
        return False
    
    try:
        # Simple query to test connection
        res = db.table("history").select("id").limit(1).execute()
        print("✅ DB connection test PASSED")
        return True
    except Exception as e:
        print(f"❌ DB connection test FAILED: {type(e).__name__}: {str(e)}")
        return False


# Optional: Test connection on import
if __name__ == "__main__":
    print("\n🧪 Testing database connection...")
    test_connection()
