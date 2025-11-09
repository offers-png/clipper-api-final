# workers.py — background tasks (cleanup old files)

import os, time, asyncio
from datetime import datetime, timedelta
from utils import PREVIEW_DIR, EXPORT_DIR

CLEAN_INTERVAL_SEC = 60 * 60  # hourly
MAX_AGE_DAYS = 7

async def _cleanup_once():
    cutoff = (datetime.utcnow() - timedelta(days=MAX_AGE_DAYS)).timestamp()
    removed = 0
    for root in (PREVIEW_DIR, EXPORT_DIR):
        try:
            for name in os.listdir(root):
                path = os.path.join(root, name)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                        os.remove(path); removed += 1
                except Exception:
                    pass
        except Exception:
            pass
    if removed:
        print(f"🧹 Removed {removed} old files")

async def start_cleanup_task():
    while True:
        try:
            await _cleanup_once()
        except Exception:
            pass
        await asyncio.sleep(CLEAN_INTERVAL_SEC)
