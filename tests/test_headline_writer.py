"""OpenAI thumbnail headlines: same rules as local ones, local fallback on any problem."""
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from clipforge_studios.backend import headline_writer, thumbnail_ai as thumb

SOURCE = ('You were halfway through a great story. Then you saw their face and realized it was their story. They told it to you. '
          'Why does this happen? Memory saves the story, but often loses the source. Your brain keeps the interesting details.')
QUOTES = ['You were halfway through a great story.', 'Why does this happen?', 'Then you saw their face and realized it was their']


class FakeOpenAI:
    def __init__(self, headlines=None, error=None):
        self.calls, self.headlines, self.error = [], headlines, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **options):
        self.calls.append(options)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'headlines': self.headlines})))])


def analysis():
    local = ['You Were Halfway', 'Why Does This Happen?', 'Then You Saw Their Face']
    return dict(topic='Memory', concepts=[dict(title=t, evidence=q) for t, q in zip(local, QUOTES)])


class HeadlineWriterTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-test', 'CLIPFORGE_HEADLINE_PROVIDER': '', 'CLIPFORGE_HEADLINE_MODEL': ''})
        env.start(); self.addCleanup(env.stop)

    def test_one_request_with_only_quotes_and_an_excerpt_using_the_cheapest_model(self):
        client = FakeOpenAI(['A', 'B', 'C'])
        moments = list(zip(thumb.STYLES.values(), QUOTES))
        self.assertEqual(headline_writer.request_headlines(moments, SOURCE * 50, thumb.HEADLINE_RULES, client), ['A', 'B', 'C'])
        self.assertEqual(len(client.calls), 1)
        options = client.calls[0]
        self.assertEqual(options['model'], 'gpt-5-nano')
        self.assertEqual(options['reasoning_effort'], 'minimal')
        self.assertNotIn('temperature', options)
        self.assertTrue(options['response_format']['json_schema']['strict'])
        sent = json.loads(options['messages'][1]['content'])
        self.assertEqual([m['quote'] for m in sent['moments']], QUOTES)
        self.assertEqual([m['style'] for m in sent['moments']], list(thumb.STYLES.values()))
        self.assertLessEqual(len(sent['transcript']), headline_writer.EXCERPT_CHARS)

    def test_non_reasoning_model_uses_temperature(self):
        client = FakeOpenAI(['A', 'B', 'C'])
        with patch.dict(os.environ, {'CLIPFORGE_HEADLINE_MODEL': 'gpt-4o-mini'}):
            headline_writer.request_headlines([('s', 'q')], SOURCE, '', client)
        self.assertEqual(client.calls[0]['model'], 'gpt-4o-mini')
        self.assertEqual(client.calls[0]['temperature'], 0.8)
        self.assertNotIn('reasoning_effort', client.calls[0])

    def test_short_answers_are_padded(self):
        self.assertEqual(headline_writer.request_headlines([('s', 'q')] * 3, SOURCE, '', FakeOpenAI(['Only One'])),
                         ['Only One', None, None])

    def test_valid_openai_headlines_replace_local_ones(self):
        result = analysis()
        with patch.object(headline_writer, 'request_headlines', return_value=["The Story Wasn't Yours", 'Why Do We Forget Sources?', 'Who Really Told It?']):
            thumb.improve_headlines(result, SOURCE)
        self.assertEqual([c['title'] for c in result['concepts']], ["The Story Wasn't Yours", 'Why Do We Forget Sources?', 'Who Really Told It?'])
        self.assertEqual([c['evidence'] for c in result['concepts']], QUOTES)  # exact quotes untouched
        self.assertEqual(result['headline_engine'], 'OpenAI gpt-5-nano')

    def test_headlines_breaking_the_rules_keep_the_local_title(self):
        result = analysis()
        bad = ['Then You Saw Their', 'A Moment of Truth', 'Why Do We Forget Sources?']  # cut off, filler, valid
        with patch.object(headline_writer, 'request_headlines', return_value=bad):
            thumb.improve_headlines(result, SOURCE)
        self.assertEqual([c['title'] for c in result['concepts']], ['You Were Halfway', 'Why Does This Happen?', 'Why Do We Forget Sources?'])
        self.assertEqual(result['headline_engine'], 'OpenAI gpt-5-nano (1 of 3; rest local)')

    def test_duplicate_of_a_local_title_falls_back_to_the_quote(self):
        result = analysis()
        with patch.object(headline_writer, 'request_headlines', return_value=['Why Does This Happen?', None, None]):
            thumb.improve_headlines(result, SOURCE)
        titles = [c['title'] for c in result['concepts']]
        self.assertEqual(titles[0], 'Why Does This Happen?')
        self.assertEqual(len({t.casefold() for t in titles}), 3)
        self.assertEqual(titles[1], 'Why Does This Happen')  # local title already used; built from its quote

    def test_openai_errors_keep_all_local_titles(self):
        result = analysis()
        with patch.object(headline_writer, 'request_headlines', side_effect=TimeoutError('slow')):
            thumb.improve_headlines(result, SOURCE)
        self.assertEqual([c['title'] for c in result['concepts']], ['You Were Halfway', 'Why Does This Happen?', 'Then You Saw Their Face'])
        self.assertIn('OpenAI unavailable', result['headline_engine'])

    def test_no_key_or_local_setting_never_calls_openai(self):
        for env in ({'OPENAI_API_KEY': ''}, {'CLIPFORGE_HEADLINE_PROVIDER': 'local'}):
            with self.subTest(env=env), patch.dict(os.environ, env), patch.object(headline_writer, 'request_headlines') as call:
                result = thumb.improve_headlines(analysis(), SOURCE)
                call.assert_not_called()
                self.assertTrue(result['headline_engine'].startswith('local'))
                self.assertEqual(headline_writer.status()['provider'], 'local')


if __name__ == '__main__':
    unittest.main()
