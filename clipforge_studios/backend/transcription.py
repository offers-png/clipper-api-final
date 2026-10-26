"""Offline-only faster-whisper. Model downloads are never automatic."""
import math
import os
from pathlib import Path
import threading
import subprocess
import re
import gc
from .inference_lock import lock as inference_lock
from . import studio_memory

from .resource_paths import MODELS
MODEL_PATH = Path(os.getenv('CLIPFORGE_LOCAL_MODEL') or MODELS/'base.en').resolve()
_model = None
_lock = threading.Lock()


def status():
    import importlib.util
    installed=importlib.util.find_spec('faster_whisper') is not None
    present=(MODEL_PATH/'model.bin').is_file()
    return dict(ready=installed and present,engine='faster-whisper',model=MODEL_PATH.name,
                library_installed=installed,model_installed=present,download_automatic=False)


def release_memory():
    """Fully unload native Whisper weights and the local VAD cache; retain transcript data."""
    global _model
    with inference_lock, _lock:
        before=studio_memory.working_set(os.getpid())
        free_before=studio_memory.available()
        was_loaded=_model is not None
        native_unloaded=False
        if _model is not None:
            _model.model.unload_model(to_cpu=False)
            native_unloaded=True
            _model=None
        # The ONNX session also holds native buffers/thread pools after transcription.
        import sys
        vad=sys.modules.get('faster_whisper.vad')
        if vad is not None:vad.get_vad_model.cache_clear()
        collected=gc.collect()
        return dict(model_was_loaded=was_loaded,native_model_unloaded=native_unloaded,
            vad_cache_cleared=vad is not None,gc_collected=collected,
            process_working_set_before_bytes=before,
            process_working_set_after_bytes=studio_memory.working_set(os.getpid()),
            available_before_bytes=free_before,available_after_bytes=studio_memory.available())


def refine_sentence_ends(audio, words):
    """Correct early sentence tails only where a detected pause separates speech.

    Retain raw model values for auditing. Do not extend into a following word
    in continuous speech; require the next word to reach the next speech region.
    """
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    regions=get_speech_timestamps(audio,VadOptions(min_silence_duration_ms=300,speech_pad_ms=0))
    for i,word in enumerate(words):
        if not re.search(r'[.!?,;:]["\u201d\']?$',word['word']):continue
        for j,region in enumerate(regions):
            end=region['end']/16000
            if not word['start'] < end or not 0 < end-word['end'] <= .5:continue
            following=regions[j+1]['start']/16000 if j+1<len(regions) else len(audio)/16000
            if following-end < .3:continue
            if i+1<len(words) and words[i+1]['end'] < following-.15:continue
            word['raw_end']=word['end']
            word['end']=end
            if i+1<len(words) and words[i+1]['start']<end:
                words[i+1]['raw_start']=words[i+1]['start']
                words[i+1]['start']=end
            break


def transcribe(audio_path):
    global _model
    if not (MODEL_PATH/'model.bin').is_file():
        raise RuntimeError('Local speech model is not installed. Approve the model download or set CLIPFORGE_LOCAL_MODEL to an existing model directory. No automatic downloads.')
    import numpy as np
    from faster_whisper import WhisperModel
    with inference_lock, _lock:
        if _model is None:
            _model = WhisperModel(str(MODEL_PATH), device='cpu', compute_type='int8',
                local_files_only=True, cpu_threads=min(8, os.cpu_count() or 2))
        # Decode with the installed FFmpeg. PyAV 19 removed a keyword that
        # faster-whisper 1.2.1 still passes; PCM input avoids that incompatible path.
        pcm = subprocess.check_output(['ffmpeg','-v','error','-i',str(audio_path),
            '-vn','-af','aresample=async=1:first_pts=0','-ar','16000','-ac','1','-f','f32le','-'])
        audio = np.frombuffer(pcm,dtype='<f4').copy()
        # Built-in Silero VAD is bundled locally. faster-whisper restores every
        # word from the speech chunks to the ORIGINAL audio timeline afterward.
        segments, info = _model.transcribe(audio, word_timestamps=True,
            beam_size=5, vad_filter=True,
            vad_parameters={'min_silence_duration_ms':500,'speech_pad_ms':150},
            condition_on_previous_text=False,
            language='en' if MODEL_PATH.name.endswith('.en') else None)
        words, rows = [], []
        previous = 0.0
        for segment in segments:
            first = len(words)
            for word in segment.words or []:
                start, end = float(word.start), float(word.end)
                if not all(math.isfinite(t) for t in (start,end)) or start < previous-.02 or end <= start:
                    continue
                words.append(dict(word=word.word.strip(), start=start, end=end,
                                  probability=float(word.probability)))
                previous = start
            if len(words) > first:
                rows.append(dict(first=first,last=len(words)-1,text=segment.text.strip(),
                                 start=words[first]['start'],end=words[-1]['end']))
        refine_sentence_ends(audio,words)
        for row in rows:
            row['start']=words[row['first']]['start']
            row['end']=words[row['last']]['end']
        return dict(text=' '.join(w['word'] for w in words), words=words, segments=rows,
            language=info.language, engine='faster-whisper', model=MODEL_PATH.name,
            timestamp_method='model word alignment with original-timeline restoration and pause-bounded sentence-tail refinement; raw adjusted values retained')
