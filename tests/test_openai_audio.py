"""OpenAI audio: mono 32 kbps, split under the upload limit, timestamps shifted per part."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import openai_audio
from url_media import MediaError


def make_media(path, seconds, audio=True):
    inputs = ['-f', 'lavfi', '-i', f'color=c=black:s=64x64:d={seconds}']
    if audio:  # stereo, so the mono conversion is observable
        inputs += ['-f', 'lavfi', '-i', f'sine=f=440:d={seconds}', '-f', 'lavfi', '-i', f'sine=f=660:d={seconds}',
                   '-filter_complex', '[1:a][2:a]amerge=inputs=2[a]', '-map', '0:v', '-map', '[a]']
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *inputs, '-shortest', '-c:v', 'libx264', '-preset', 'ultrafast', str(path)],
                   check=True, timeout=120)


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=channels,bit_rate,sample_rate:format=duration',
                          '-of', 'json', str(path)], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    return data['streams'][0], float(data['format']['duration'])


class FakeWhisper:
    """Answers like OpenAI verbose_json: segment times relative to the uploaded part."""
    def __init__(self, as_object=False):
        self.calls, self.as_object = [], as_object
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.create))

    def create(self, model, file, response_format):
        index = len(self.calls)
        _, length = probe(file.name)
        self.calls.append(dict(model=model, response_format=response_format, size=Path(file.name).stat().st_size, length=length))
        segments = [dict(start=0.5, end=2.0, text=f' part {index} start'), dict(start=length - 2, end=length - 0.5, text=f' part {index} end')]
        if self.as_object:
            return SimpleNamespace(text=f'Part {index}.', segments=[SimpleNamespace(**s) for s in segments])
        return dict(text=f'Part {index}.', segments=segments)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class OpenAiAudioTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_audio_is_mono_32_kbps_and_one_part_when_small(self):
        source = self.folder / 'stereo.mp4'
        make_media(source, 60)
        (self.folder / 'work').mkdir()
        parts = openai_audio.prepare_parts(source, self.folder / 'work')
        self.assertEqual(len(parts), 1)
        stream, length = probe(parts[0][0])
        self.assertEqual(stream['channels'], 1)
        self.assertEqual(int(stream['bit_rate']), 32000)
        self.assertAlmostEqual(length, 60, delta=0.2)
        self.assertLess(parts[0][0].stat().st_size, 60 * 32000 / 8 * 1.05)

    def test_large_audio_is_split_under_the_limit_into_contiguous_parts(self):
        source = self.folder / 'three-minutes.mp4'
        make_media(source, 180)
        limit = 300 * 1024  # 180 s at 32 kbps is about 720 KB, so at least 3 parts
        (self.folder / 'work').mkdir()
        parts = openai_audio.prepare_parts(source, self.folder / 'work', limit)
        self.assertGreaterEqual(len(parts), 3)
        lengths = [probe(path)[1] for path, _ in parts]
        for (path, start), length, i in zip(parts, lengths, range(len(parts))):
            self.assertLessEqual(path.stat().st_size, limit)
            self.assertAlmostEqual(start, i * 180 / len(parts), delta=0.05)  # MP3 padding adds ~1 ms
        # Parts are back to back: each starts where the previous one ends, covering the whole source.
        for (_, start), (_, next_start), length in zip(parts, parts[1:], lengths):
            self.assertAlmostEqual(start + length, next_start, delta=0.1)
        self.assertAlmostEqual(sum(lengths), 180, delta=0.3)

    def test_transcript_parts_are_joined_in_order_with_source_timestamps(self):
        source = self.folder / 'three-minutes.mp4'
        make_media(source, 180)
        for as_object in (False, True):
            with self.subTest(sdk_object=as_object):
                whisper = FakeWhisper(as_object)
                text, segments = openai_audio.transcribe(whisper, source, max_bytes=300 * 1024)
                count = len(whisper.calls)
                self.assertGreaterEqual(count, 3)
                self.assertTrue(all(c['model'] == 'whisper-1' and c['response_format'] == 'verbose_json' for c in whisper.calls))
                self.assertTrue(all(c['size'] <= 300 * 1024 for c in whisper.calls))
                self.assertEqual(text, ' '.join(f'Part {i}.' for i in range(count)))
                length = 180 / count
                for i in range(count):
                    first, last = segments[2 * i], segments[2 * i + 1]
                    self.assertEqual(first['text'], f'part {i} start')
                    self.assertAlmostEqual(first['start'], i * length + 0.5, delta=0.05)
                    self.assertAlmostEqual(last['end'], i * length + whisper.calls[i]['length'] - 0.5, delta=0.05)
                starts = [s['start'] for s in segments]
                self.assertEqual(starts, sorted(starts))
                self.assertLessEqual(segments[-1]['end'], 180)

    def test_plain_text_answer_still_works(self):
        source = self.folder / 'short.mp4'
        make_media(source, 3)
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=lambda **kw: ' hello ')))
        self.assertEqual(openai_audio.transcribe(client, source), ('hello', []))

    def test_video_without_audio_is_rejected_before_calling_openai(self):
        source = self.folder / 'silent.mp4'
        make_media(source, 3, audio=False)
        whisper = FakeWhisper()
        with self.assertRaisesRegex(MediaError, 'no audio track'):
            openai_audio.transcribe(whisper, source)
        self.assertEqual(whisper.calls, [])

    def test_temporary_audio_files_are_removed(self):
        source = self.folder / 'short.mp4'
        make_media(source, 3)
        seen = []
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(
            create=lambda **kw: seen.append(kw['file'].name) or 'ok')))
        openai_audio.transcribe(client, source)
        self.assertFalse(Path(seen[0]).exists())


if __name__ == '__main__':
    unittest.main()
