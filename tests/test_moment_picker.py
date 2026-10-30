"""OpenAI moment picking and word transcription: checked like local results, local models only without a key."""
import json
import os
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

    def test_rule_breaking_or_long_headlines_fall_back_to_the_topic_not_the_quote(self):
        bad = reply(headlines=('A Moment of Truth', 'Why Does This Keep Happening?', 'Memory Saves The Story'))
        with patch.object(moment_picker, 'pick', return_value=(bad, None)) as pick:
            result = thumb.openai_plan(words(), [], {})
        self.assertIn('2 to 4 words', pick.call_args.args[2])
        self.assertEqual(result['concepts'][0]['title'], 'How Memory Loses Sources')
        self.assertIsNone(result['concepts'][0]['key_word'])
        self.assertEqual(result['concepts'][1]['title'], 'Why Does This Happen')  # topic already used; built from the quote
        self.assertEqual(result['headline_engine'], 'OpenAI gpt-4o-mini (1 of 3; rest from the topic)')

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
        with patch.object(openai_audio, 'prepare_parts', return_value=[(os.devnull, 0.0), (os.devnull, 60.0)]):
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

    def test_words_missing_from_the_text_do_not_shift_the_marks(self):
        words = [dict(word=w, start=0, end=1) for w in ['so', 'um', 'it', 'works', 'Great']]
        openai_audio.add_sentence_marks(words, [dict(text='so it works. Great.')])
        self.assertEqual([w['word'] for w in words], ['so', 'um', 'it', 'works.', 'Great.'])


if __name__ == '__main__':
    unittest.main()
