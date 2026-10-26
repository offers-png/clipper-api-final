"""Isolated Linux CPU probe; fixed public CC0 video/JFK speech, no KIE access."""
import json
from pathlib import Path
import subprocess
import uuid
import requests
from clipforge_studios.backend import thumbnail_ai as thumb, thumbnail_vision, studio_ai, studio_memory
from clipforge_studios.backend.resource_paths import DATA

def public_fixture(url,target):
    session=requests.Session();session.trust_env=False
    with session.get(url,stream=True,timeout=(15,60)) as r:
        r.raise_for_status();size=0
        with target.open('wb') as output:
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>5_000_000:raise ValueError('Fixture exceeds size limit')
                output.write(chunk)

def main():
    audio=DATA/'jfk.flac';video=DATA/'flower.mp4'
    public_fixture('https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/tests/data/jfk.flac',audio)
    public_fixture('https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4',video)
    words=thumb.speech_words(dict(source=str(audio)),dict(id=uuid.uuid4().hex))
    frames=[]
    for i,t in enumerate((1,3)):
        image=DATA/f'flower-{i}.jpg'
        subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=640:-2',str(image)],check=True)
        frames.append(dict(time=t,path=str(image)))
    observed=thumbnail_vision.describe(frames)
    # Independent fixtures test visual recognition and speech separately; do
    # not invent a relationship between flowers and the JFK speech.
    concepts=thumb.plan(words)
    output=dict(speech_text=' '.join(w['word'] for w in words),speech_words=len(words),
        visual_observations=observed,concept_titles=[c['title'] for c in concepts['concepts']],
        memory_metrics=studio_ai.status()['memory_metrics'],container_memory_bytes=studio_memory.container_limit()[0],
        available_after_bytes=studio_memory.available(),paid_requests=0)
    (DATA/'linux-probe-result.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps(output),flush=True)

if __name__=='__main__':main()
