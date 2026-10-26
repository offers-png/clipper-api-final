"""Shared speech isolation contracts. Real-media tests run separately."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from clipforge_studios.backend import transcription as speech

class SpeechRuntimeTests(unittest.TestCase):
 def test_external_worker_receives_no_credentials_and_returns_word_alignment(self):
  with tempfile.TemporaryDirectory() as folder:
   model=Path(folder);(model/'model.bin').touch()
   words=[dict(word='Actual',start=.12,end=.45)]
   def run(args,**kwargs):
    self.assertEqual(args[1:3],['-m','clipforge_studios.backend.thumbnail_speech'])
    self.assertNotIn('CLIPFORGE_VIDEO_PROVIDER_API_KEY',kwargs['env'])
    self.assertNotIn('CLIPFORGE_THUMBNAIL_SPEECH_PYTHON',kwargs['env'])
    self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'],'1')
    Path(args[-1]).write_text(json.dumps(dict(words=words)),encoding='utf-8')
    return subprocess.CompletedProcess(args,0)
   with patch.object(speech,'MODEL_PATH',model),patch.object(speech,'SPEECH_PYTHON',model/'python'),patch.object(speech.subprocess,'run',side_effect=run),patch.dict(os.environ,{'CLIPFORGE_VIDEO_PROVIDER_API_KEY':'contract-test'}):
    self.assertEqual(speech.transcribe('audio.wav')['words'],words)
 def test_failed_worker_stops_without_cloud_fallback(self):
  with tempfile.TemporaryDirectory() as folder:
   model=Path(folder);(model/'model.bin').touch()
   with patch.object(speech,'MODEL_PATH',model),patch.object(speech,'SPEECH_PYTHON',model/'python'),patch.object(speech.subprocess,'run',return_value=subprocess.CompletedProcess([],1)):
    with self.assertRaisesRegex(RuntimeError,'No cloud fallback'):speech.transcribe('audio.wav')
 def test_capabilities_use_installed_isolated_library(self):
  with tempfile.TemporaryDirectory() as folder:
   model=Path(folder);(model/'model.bin').touch()
   with patch.object(speech,'MODEL_PATH',model),patch.object(speech,'SPEECH_PYTHON',model/'python'),patch.object(speech,'external_library',return_value=True):
    self.assertTrue(speech.status()['ready'])
