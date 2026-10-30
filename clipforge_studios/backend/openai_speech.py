"""OpenAI Whisper transcripts for the Studio tools when OPENAI_API_KEY is set.

Same switch as the thumbnail headlines (headline_writer.enabled). Produces the
same transcript shape as transcription.transcribe, so callers treat both alike;
the offline faster-whisper model remains the path without a key.
"""
import re
from . import headline_writer
from .moment_picker import WHISPER_USD_PER_MINUTE

ENGINE = 'openai-whisper'
MODEL = 'whisper-1'
enabled = headline_writer.enabled


def has_audio(path):
    from url_media import streams
    return any(s.get('codec_type') == 'audio' for s in streams(str(path)))


def words(path):
    """[{word, start, end}] for a media file, with sentence punctuation."""
    from openai import OpenAI
    from openai_audio import transcribe_words
    return transcribe_words(OpenAI(timeout=600, max_retries=1), str(path))


def transcript(path, duration):
    """(transcript dict like transcription.transcribe, USD) for a media file."""
    found = words(path)
    for word in found:  # Whisper can give a word no length; callers require start < end
        word['end'] = max(word['end'], round(word['start'] + .02, 3))
    rows, first = [], 0
    for i, word in enumerate(found):
        if re.search(r'[.!?]$', word['word']) or i == len(found) - 1:
            rows.append(dict(first=first, last=i, text=' '.join(w['word'] for w in found[first:i + 1]),
                             start=found[first]['start'], end=word['end']))
            first = i + 1
    return (dict(text=' '.join(w['word'] for w in found), words=found, segments=rows, language='en', engine=ENGINE,
                 model=MODEL, alignment_version=3, timestamp_method='OpenAI Whisper word timestamps'),
            round(duration / 60 * WHISPER_USD_PER_MINUTE, 4))


def source_words(source):
    """(words, USD) for a Studio upload, kept in source['openai_words'] so a later tool or
    rerun is not billed again; [] for silent sources. Callers save the changed source."""
    if 'openai_words' in source:
        return source['openai_words'], 0
    if not has_audio(source['source']):
        return [], 0
    source['openai_words'] = words(source['source'])
    return source['openai_words'], round(source['duration'] / 60 * WHISPER_USD_PER_MINUTE, 4)
