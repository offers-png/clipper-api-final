"""OpenAI moment picking and word transcription: checked like local results, local models only without a key."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from clipforge_studios.backend import moment_picker, thumbnail_ai as thumb
import openai_audio

SENTENCES = ['You were halfway through a great story.', 'Then you saw their face.', 'Why does this happen?',
             'Memory saves the story.', 'Your brain keeps the details.']


def words():
    result, t = [], 0.0
    for sentence in SENTENCES:
        for word in sentence.split():
            result.append(dict(word=word, start=round(t, 2), end=round(t + 0.4, 2))); t += 0.5
    return result


class FakeChat:
    def __init__(self, reply, usage=SimpleNamespace(prompt_tokens=10_000, completion_tokens=2_000)):
        self.calls, self.reply, self.usage = [], reply, usage
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **options):
        self.calls.append(options)
        return SimpleNamespace(usage=self.usage, choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.reply)))])


def reply(ids=(0, 2, 3), headlines=('Halfway Through A Story', 'Why Does This Happen?', 'Memory Saves The Story')):
    return dict(topic='How Memory Loses Sources', concepts=[dict(phrase_id=i, headline=h, key_word=h.split()[-1].strip('?'), rationale='r', image_prompt='p')
                                                  for i, h in zip(ids, headlines)])


class MomentPickerTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-test', 'CLIPFORGE_HEADLINE_PROVIDER': '', 'CLIPFORGE_HEADLINE_MODEL': ''})
        env.start(); self.addCleanup(env.stop)

    def test_one_strict_request_with_numbered_phrases_and_its_cost(self):
        client = FakeChat(reply())
        phrases = [dict(id=0, text='a b'), dict(id=1, text='c d')]
        result, usd = moment_picker.pick(phrases, thumb.STYLES, thumb.HEADLINE_RULES, [dict(time=1, description='a desk')], client)
        self.assertEqual(result['topic'], 'How Memory Loses Sources')
        self.assertIn("about the video's topic and promise", client.calls[0]['messages'][0]['content'])
        self.assertIn('key_word', client.calls[0]['response_format']['json_schema']['schema']['properties']['concepts']['items']['required'])
        options = client.calls[0]
        self.assertEqual(options['model'], 'gpt-4o-mini')
        self.assertEqual(options['temperature'], 0.4)
        self.assertNotIn('reasoning_effort', options)
        self.assertTrue(options['response_format']['json_schema']['strict'])
        sent = json.loads(options['messages'][1]['content'])
        self.assertEqual(sent['phrases'], phrases)
        self.assertEqual([d['approach'] for d in sent['designs']], list(thumb.STYLES))
        self.assertAlmostEqual(usd, (10_000 * 0.15 + 2_000 * 0.60) / 1_000_000)

    def test_reasoning_model_override_uses_low_effort(self):
        client = FakeChat(reply())
        with patch.dict(os.environ, {'CLIPFORGE_HEADLINE_MODEL': 'gpt-5-nano'}):
            _, usd = moment_picker.pick([], thumb.STYLES, '', (), client)
        self.assertEqual(client.calls[0]['model'], 'gpt-5-nano')
        self.assertEqual(client.calls[0]['reasoning_effort'], 'low')
        self.assertNotIn('temperature', client.calls[0])
        self.assertAlmostEqual(usd, (10_000 * 0.05 + 2_000 * 0.40) / 1_000_000)

    def test_each_design_gets_its_part_of_the_video(self):
        rows = thumb.phrases(words())  # starts 0, 3.5, 6, 8, 10; ends 12.4
        self.assertEqual(thumb.thirds(rows), [(0, 1), (2, 3), (4, 4)])
        self.assertIsNone(thumb.thirds(rows[:1]))  # too short to split
        concepts = reply(ids=(0, 2, 4))['concepts']
        answer = dict(topic='Memory', beginning=dict(concepts[0], phrase_id='B0'), middle=dict(concepts[1], phrase_id='M2'),
                      end=dict(concepts[2], phrase_id='E4'))
        client = FakeChat(answer)
        result, _ = moment_picker.pick([dict(id=r['id'], text=r['text']) for r in rows], thumb.STYLES, '', (), client,
                                       ranges=thumb.thirds(rows))
        sent = json.loads(client.calls[0]['messages'][1]['content'])
        self.assertEqual([p['id'] for p in sent['phrases']], ['B0', 'B1', 'M2', 'M3', 'E4'])
        self.assertEqual([d['part'] for d in sent['designs']], ['beginning', 'middle', 'end'])
        schema = client.calls[0]['response_format']['json_schema']['schema']['properties']
        self.assertEqual([schema[p]['properties']['phrase_id']['pattern'] for p in ('beginning', 'middle', 'end')],
                         ['^B[0-9]+$', '^M[0-9]+$', '^E[0-9]+$'])
        self.assertEqual([c['phrase_id'] for c in result['concepts']], [0, 2, 4])
        self.assertEqual(result['concepts'][1]['headline'], 'Why Does This Happen?')

    def test_a_pick_outside_its_part_is_flagged(self):
        job = {}
        with patch.object(moment_picker, 'pick', return_value=(reply(ids=(0, 2, 3)), None)) as pick:
            thumb.openai_plan(words(), [], job)
        self.assertEqual(pick.call_args.kwargs['ranges'], [(0, 1), (2, 3), (4, 4)])
        self.assertEqual(job['warnings'], ['Concept 3 is not from its part of the video (beginning, middle, end).'])

    def test_unknown_model_price_reports_no_cost(self):
        self.assertIsNone(moment_picker.cost('some-new-model', SimpleNamespace(prompt_tokens=1, completion_tokens=1)))

    def test_picks_become_concepts_with_exact_phrase_times(self):
        job = {}
        with patch.object(moment_picker, 'pick', return_value=(reply(), 0.0012)):
            result = thumb.openai_plan(words(), [], job)
        rows = thumb.phrases(words())
        self.assertEqual([c['evidence'] for c in result['concepts']], [rows[i]['text'] for i in (0, 2, 3)])
        self.assertEqual([(c['time'], c['end']) for c in result['concepts']], [(rows[i]['start'], rows[i]['end']) for i in (0, 2, 3)])
        self.assertEqual([c['title'] for c in result['concepts']], ['Halfway Through A Story', 'Why Does This Happen?', 'Memory Saves The Story'])
        self.assertEqual([c['key_word'] for c in result['concepts']], ['Story', 'Happen', 'Story'])
        self.assertEqual(result['topic'], 'How Memory Loses Sources')
        self.assertEqual(result['headline_engine'], 'OpenAI gpt-4o-mini')
        self.assertEqual(job['cost_usd'], {'moments': 0.0012})

    def test_rejected_headlines_are_retried_with_openai_then_built_from_the_topic(self):
        bad = reply(headlines=('A Moment of Truth', 'Why Does This Keep Happening?', 'Memory Saves The Story'))  # filler, 5 words, valid
        job = {}
        with patch.object(moment_picker, 'pick', return_value=(bad, 0.001)) as pick, \
                patch.object(moment_picker, 'rewrite_headlines', return_value=(['Why Memory Fails?', 'A Moment of Truth'], 0.0002)) as rewrite:
            result = thumb.openai_plan(words(), [], job)
        self.assertIn('2 to 4 words', pick.call_args.args[2])
        topic, styles, rules, avoid = rewrite.call_args.args
        self.assertEqual((topic, len(styles), avoid), ('How Memory Loses Sources', 2, ['memory saves the story']))
        self.assertEqual([c['title'] for c in result['concepts']], ['Why Memory Fails?', 'How Memory Loses Sources', 'Memory Saves The Story'])
        self.assertIsNone(result['concepts'][0]['key_word'])
        self.assertEqual(result['headline_engine'], 'OpenAI gpt-4o-mini (1 rewritten, 1 from the topic)')
        self.assertAlmostEqual(job['cost_usd']['moments'], 0.0012)

    def test_failed_retry_still_never_uses_a_quote(self):
        bad = dict(reply(headlines=('A Moment of Truth',) * 3), topic='Memory And Sources')
        with patch.object(moment_picker, 'pick', return_value=(bad, None)), \
                patch.object(moment_picker, 'rewrite_headlines', side_effect=TimeoutError('slow')):
            result = thumb.openai_plan(words(), [], {})
        # "sources" is never said in the video, so variations use "memory", which is
        self.assertEqual([c['title'] for c in result['concepts']], ['Memory And Sources', 'Memory Explained', 'Inside Memory'])
        self.assertEqual(result['headline_engine'], 'OpenAI gpt-4o-mini (0 rewritten, 3 from the topic)')

    def test_rewrite_asks_for_new_topic_headlines_unlike_the_used_ones(self):
        client = FakeChat(None)
        client.create = lambda **options: (client.calls.append(options), SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'headlines': ['Why Memory Fails?']})))]))[1]
        client.chat = SimpleNamespace(completions=SimpleNamespace(create=client.create))
        headlines, usd = moment_picker.rewrite_headlines('Memory', ['a question'], 'rules. ', ['memory saves the story'], client)
        self.assertEqual(headlines, ['Why Memory Fails?'])
        sent = json.loads(client.calls[0]['messages'][1]['content'])
        self.assertEqual(sent, dict(topic='Memory', styles=['a question'], avoid=['memory saves the story']))
        self.assertTrue(client.calls[0]['response_format']['json_schema']['strict'])

    def test_saved_transcript_is_reused_without_a_new_charge(self):
        job, source = {}, dict(openai_words=words(), source='video.mp4', duration=60.0)
        with patch('openai_audio.transcribe_words') as transcribe:
            self.assertEqual(thumb.openai_speech_words(source, job), words())
        transcribe.assert_not_called()
        self.assertEqual(job['cost_usd'], {'transcription': 0})

    def test_unknown_repeated_or_missing_picks_are_rejected(self):
        for bad in (reply(ids=(0, 2, 99)), reply(ids=(0, 2, 2)), dict(topic='x', concepts=reply()['concepts'][:2])):
            with self.subTest(bad=bad), patch.object(moment_picker, 'pick', return_value=(bad, None)):
                with self.assertRaises(ValueError):
                    thumb.openai_plan(words(), [], {})

    def test_switch_follows_the_key_and_local_setting(self):
        self.assertTrue(moment_picker.enabled())
        for env in ({'OPENAI_API_KEY': ''}, {'CLIPFORGE_HEADLINE_PROVIDER': 'local'}):
            with self.subTest(env=env), patch.dict(os.environ, env):
                self.assertFalse(moment_picker.enabled())


class TranscribeWordsTests(unittest.TestCase):
    def test_words_get_offsets_and_sentence_punctuation(self):
        results = [dict(words=[dict(word=' Hello', start=0.0, end=0.4), dict(word='there', start=0.5, end=0.9),
                               dict(word='Why', start=1.0, end=1.2), dict(word='not', start=1.3, end=1.6)],
                        segments=[dict(text=' Hello there.', end=0.9), dict(text='Why not?', end=1.6)]),
                   dict(words=[dict(word='Bye', start=0.0, end=0.3)], segments=[dict(text='Bye!', end=0.3)])]
        calls = []

        def create(**options):
            calls.append(options); return results[len(calls) - 1]
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        with patch.object(openai_audio, 'word_parts', return_value=[(os.devnull, 0.0), (os.devnull, 60.0)]), \
                patch.object(openai_audio, 'PARALLEL_PARTS', 1):  # answers are matched to calls by order
            result = openai_audio.transcribe_words(client, 'video.mp4')
        self.assertEqual([w['word'] for w in result], ['Hello', 'there.', 'Why', 'not?', 'Bye!'])
        self.assertEqual(result[-1], dict(word='Bye!', start=60.0, end=60.3))
        self.assertEqual(calls[0]['timestamp_granularities'], ['word', 'segment'])

    def test_sentences_ending_mid_segment_keep_their_marks(self):
        words = [dict(word=w, start=i, end=i + .5) for i, w in enumerate(
            ['a', 'line', 'of', 'code', 'AI', 'agents', 'broke', "you'll", 'see', 'Why', 'not'])]
        segments = [dict(text=' a line of code. AI agents broke,'), dict(text=" you'll see! Why not?\"")]
        openai_audio.add_sentence_marks(words, segments)
        self.assertEqual(' '.join(w['word'] for w in words), "a line of code. AI agents broke you'll see! Why not?")

    def test_long_audio_is_cut_at_pauses_into_parts_of_at_most_ten_minutes(self):
        self.assertEqual(openai_audio.cut_points(300, []), [0.0, 300])  # short: one part
        self.assertEqual(openai_audio.cut_points(1671, [100, 570, 590.5, 640, 1150, 1185]), [0.0, 590.5, 1185, 1671])
        self.assertEqual(openai_audio.cut_points(1300, []), [0.0, 600, 1200, 1300])  # no pauses: cut at the limit

    def test_parts_are_transcribed_together_and_kept_in_order(self):
        def create(file, **options):
            # Later parts answer first; the result must still be in time order.
            offset = 1 if file.name.endswith('a') else 0
            return dict(words=[dict(word=f'w{offset}', start=1.0, end=2.0)], segments=[dict(text=f'w{offset}.')])
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder) / 'part0', Path(folder) / 'part1a'
            first.write_bytes(b''); second.write_bytes(b'')
            with patch.object(openai_audio, 'word_parts', return_value=[(first, 0.0), (second, 600.0)]):
                words = openai_audio.transcribe_words(client, 'video.mp4')
        self.assertEqual(words, [dict(word='w0.', start=1.0, end=2.0), dict(word='w1.', start=601.0, end=602.0)])

    def test_a_long_mismatch_does_not_stop_later_marks(self):
        # The text has a run of words Whisper's word list lacks; marks after it must still land.
        spoken = 'first part ends here. then more talk. last bit ends. final words now.'.split()
        words = [dict(word=w.rstrip('.'), start=0, end=1) for w in spoken]
        noise = ' '.join(f'zz{i}' for i in range(12))
        segments = [dict(text='first part ends here. ' + noise), dict(text='then more talk. last bit ends. final words now.')]
        openai_audio.add_sentence_marks(words, segments)
        self.assertEqual(' '.join(w['word'] for w in words), ' '.join(spoken))

    def test_words_missing_from_the_text_do_not_shift_the_marks(self):
        words = [dict(word=w, start=0, end=1) for w in ['so', 'um', 'it', 'works', 'Great']]
        openai_audio.add_sentence_marks(words, [dict(text='so it works. Great.')])
        self.assertEqual([w['word'] for w in words], ['so', 'um', 'it', 'works.', 'Great.'])


if __name__ == '__main__':
    unittest.main()
