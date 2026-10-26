"""Thumbnail-only offline speech subprocess. Process exit frees all native RAM."""
import json
from pathlib import Path
import sys
from .transcription import transcribe

if __name__=='__main__':
    audio,output=map(Path,sys.argv[1:3])
    output.write_text(json.dumps(transcribe(audio)),encoding='utf-8')
