"""Public deployment switches; no credentials or authentication policy."""
import os


def studios_enabled():
    return os.getenv('CLIPFORGE_ENABLE_STUDIOS', '').strip().lower() in {'1', 'true', 'yes', 'on'}
