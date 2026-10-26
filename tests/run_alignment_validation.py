"""Real-media regression. No downloads, cloud requests, or mock transcripts.

Usage: python -m tests.run_alignment_validation --video ACTUAL.mp4 --speech JFK.flac
Speech fixture: SYSTRAN/faster-whisper tests/data/jfk.flac (public known dialogue).
The validation composition uses real video and recorded dialogue, not AI synthesis.
"""
import argparse
from pathlib import Path
import re
import subprocess
from clipforge_studios.backend import transcription,server,media
from clipforge_studios.backend.resource_paths import DATA

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--video',required=True);parser.add_argument('--speech',required=True)
 args=parser.parse_args();folder=DATA/'alignment-validation';folder.mkdir(exist_ok=True)
 video=folder/'known-dialogue.mp4'
 subprocess.run(['ffmpeg','-v','error','-y','-i',args.video,'-i',args.speech,'-map','0:v:0','-map','1:a:0',
                 '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(video)],check=True)
 try:
  transcript=transcription.transcribe(video)
  words=transcript['words'];first=next(i for i,w in enumerate(words) if w['word'].lower()=='ask')
  last=next(i for i in range(first,len(words)) if words[i]['word'].lower().strip(',.')=='you')
  clip=server.extract(dict(source=str(video),duration=media.ffprobe_duration(str(video)),words=words),first,last,0)
  actual=transcription.transcribe(Path(media.PREVIEW_DIR)/Path(clip['preview_url']).name)['text']
  normalize=lambda text:' '.join(re.findall(r'\w+',text.lower()))
  assert normalize(actual)==normalize(clip['text']),(actual,clip['text'])
  original=transcription.transcribe(args.video)
  assert all(w['end']>w['start'] for w in original['words'])
  print(dict(selected=clip['text'],recognized=actual,start=clip['start'],end=clip['end'],
             positive_timestamps=True,source_words=len(original['words'])))
 finally:transcription.release_memory()

if __name__=='__main__':main()
