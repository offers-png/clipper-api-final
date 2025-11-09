# db.py — Supabase helpers (best-effort; skip if not configured)

import os
from typing import Optional
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

def init_supabase() -> Optional[Client]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print("⚠️ Supabase init failed:", e)
        return None

def _client() -> Optional[Client]:
    return init_supabase()

def upsert_video_row(user_id: str, src_url: str, duration_sec: int = None, transcript: str = None):
    sb = _client()
    if not sb: return None
    payload = {
        "user_id": user_id,
        "src_url": src_url,
        "duration_sec": duration_sec,
        "transcript": transcript
    }
    try:
        return sb.table("videos").insert(payload).execute()
    except Exception as e:
        print("⚠️ upsert_video_row failed:", e)
        return None

def insert_clip_row(user_id: str, video_id: str, start_sec, end_sec, preview_url, final_url, transcript=None):
    sb = _client()
    if not sb: return None
    payload = {
        "user_id": user_id,
        "video_id": video_id,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "preview_url": preview_url,
        "final_url": final_url,
        "transcript": transcript
    }
    try:
        return sb.table("clips").insert(payload).execute()
    except Exception as e:
        print("⚠️ insert_clip_row failed:", e)
        return None
