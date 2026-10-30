"""Auto Clips Even split and viral-style finishing (captions, hook title, emoji pop-ups)."""
from pathlib import Path
import tempfile
import unittest
from clipforge_studios.backend import openai_analysis
from clipforge_studios.backend.studio import Enhancements, StudioRequest, even_ranges
from clipforge_studios.backend.studio_render import picture_box, popup_times, subtitle_groups, write_captions


def words(text, start=0.0, step=0.5):
    return [dict(word=w, start=start + i * step, end=start + i * step + 0.4) for i, w in enumerate(text.split())]


class EvenSplitTests(unittest.TestCase):
    def test_sixty_seconds_times_ten_is_the_whole_ten_minute_video(self):
        clips = even_ranges(600, 60, 10)
        self.assertEqual(len(clips), 10)
        self.assertEqual([(c['start'], c['end']) for c in clips], [(i * 60, i * 60 + 60) for i in range(10)])

    def test_thirty_seconds_times_ten_is_spread_across_the_video(self):
        clips = even_ranges(600, 30, 10)
        self.assertEqual([c['start'] for c in clips], [15 + i * 60 for i in range(10)])
        self.assertTrue(all(c['end'] - c['start'] == 30 for c in clips))

    def test_too_long_gives_a_clear_message(self):
        with self.assertRaisesRegex(ValueError, 'longer than this video'):
            even_ranges(500, 60, 10)

    def test_a_video_a_little_under_the_total_still_works(self):
        clips = even_ranges(599.5, 60, 10)
        self.assertAlmostEqual(clips[-1]['end'], 599.5)
        self.assertAlmostEqual(clips[0]['end'] - clips[0]['start'], 59.95)

    def test_request_accepts_even_mode_and_finishing(self):
        body = StudioRequest(source_id='a' * 32, mode='even', duration=30, count=10, enhancements=Enhancements(finishing=True))
        self.assertEqual((body.mode, body.count, body.enhancements.finishing), ('even', 10, True))


class FinishingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.folder = Path(temp.name)
        self.words = words('this is the best pizza in town and the sauce is so nice you will love it today')
        self.groups = subtitle_groups(self.words, 0, 10, 3, 18)

    def ass(self, popups=(), title='BEST PIZZA IN TOWN', keys=('pizza',)):
        write_captions(self.folder, self.groups, 720, 1280, True, title, '', '',
                       dict(box=(467, 872), title=title, keys=set(keys), popups=list(popups), duration=10))
        return (self.folder / 'captions.ass').read_text(encoding='utf-8-sig')

    def test_captions_are_short_big_uppercase_groups_with_the_spoken_and_key_words_colored(self):
        self.assertTrue(all(len(g) <= 3 for g in self.groups))
        text = self.ass()
        self.assertIn('Style: Big,Arial,60,', text)
        self.assertIn('{\\c&H005EEFCB&\\fscx112\\fscy112}THIS', text)  # the word being spoken
        self.assertIn('{\\c&H0000E5FF&}PIZZA', text)  # a key word
        self.assertIn(',888,1', text.split('Style: Big')[1].split('\n')[0])  # just under the picture, off the face

    def test_hook_title_shows_for_the_first_three_seconds(self):
        title = [l for l in self.ass().splitlines() if ',Title,' in l]
        self.assertEqual(len(title), 1)
        self.assertTrue(title[0].startswith('Dialogue: 2,0:00:00.00,0:00:03.00,Title') and title[0].endswith('BEST PIZZA IN TOWN'))

    def test_popups_appear_when_said_above_the_picture(self):
        text = self.ass([dict(quote='the sauce is', label='NICE SAUCE', emoji='🍕')])
        pop = [l for l in text.splitlines() if ',Pop,' in l]
        self.assertEqual(len(pop), 1)
        self.assertTrue(pop[0].startswith('Dialogue: 3,0:00:04.00,'))  # "the sauce" is said at 4.0 s
        self.assertIn('\\pos(360,413)', pop[0])  # above the picture, which starts at 467
        self.assertIn('NICE SAUCE', pop[0])

    def test_popups_wait_for_the_title_stay_apart_and_max_three(self):
        popups = [dict(quote=q, label='X', emoji='') for q in ('best pizza', 'in town', 'the sauce', 'so nice', 'love it')]
        times = [round(t, 1) for t, _ in popup_times(self.groups, popups, 10)]
        self.assertEqual(times[0], 3.0)  # "best pizza" (1.5 s) waits until the title is gone
        self.assertLessEqual(len(times), 3)
        self.assertTrue(all(b - a >= 2.2 for a, b in zip(times, times[1:])))

    def test_unspoken_popups_are_skipped(self):
        self.assertEqual(popup_times(self.groups, [dict(quote='cheese burger', label='X', emoji='')], 10), [])

    def test_popup_picture_has_a_full_color_emoji(self):
        from PIL import Image
        from clipforge_studios.backend.studio_render import COLOR_EMOJI, popup_image
        self.assertTrue(COLOR_EMOJI.is_file())  # bundled, so it works on any server
        self.assertTrue(popup_image('NICE SAUCE', '🍕', 45, self.folder / 'pop.png'))
        with Image.open(self.folder / 'pop.png') as pop:
            colors = pop.convert('RGB').getcolors(1 << 20)
            with_text_only = pop.width
        orange = sum(n for n, (r, g, b) in colors if r > 200 and 60 < g < 170 and b < 90)  # pizza crust and pepperoni
        self.assertGreater(orange, 50)
        popup_image('NICE SAUCE', '', 45, self.folder / 'plain.png')
        with Image.open(self.folder / 'plain.png') as plain:
            self.assertGreater(with_text_only, plain.width)

    def test_picture_box_for_wide_video_in_vertical_output(self):
        plan = dict(content_box=[0, 250, 720, 840], mode='safe-fit')
        self.assertEqual(picture_box(plan, dict(width=1920, height=1080)), (467.5, 872.5))


class FinishingAnalysisTests(unittest.TestCase):
    text = 'this is the best pizza in town and the sauce is so nice'

    def check(self, **fields):
        item = dict(id=1, suitable=True, score=.9, evidence='the best pizza in town', reason='r', hook_quote='', context_quote='',
                    emoji='', title='best pizza in town', key_words=['pizza', 'burger'],
                    popups=[dict(quote='the sauce is', label='nice sauce', emoji='🍕')])
        item.update(fields)
        return openai_analysis._check(item, {1: dict(id=1, text=self.text)})

    def test_title_keywords_and_popups_are_kept_when_grounded(self):
        result = self.check()
        self.assertEqual(result['title'], 'BEST PIZZA IN TOWN')
        self.assertEqual(result['key_words'], ['pizza'])  # "burger" is never said
        self.assertEqual(result['popups'], [dict(quote='the sauce is', label='NICE SAUCE', emoji='🍕')])

    def test_bad_finishing_fields_are_dropped(self):
        result = self.check(title='this title is far too long to fit', popups=[
            dict(quote='never said here', label='NOPE', emoji='🍕'), dict(quote='so nice', label='SO NICE', emoji='ok')])
        self.assertEqual(result['title'], '')
        self.assertEqual(result['popups'], [dict(quote='so nice', label='SO NICE', emoji='')])

    def test_clips_the_model_skips_are_asked_for_again(self):
        import json
        from types import SimpleNamespace
        calls = []

        def create(**options):
            ids = [c['id'] for c in json.loads(options['messages'][1]['content'])]
            calls.append(ids)
            items = [dict(id=i, suitable=True, score=.5, evidence='the best pizza in town', reason='r', hook_quote='',
                          context_quote='', emoji='', title='best pizza in town', key_words=[], popups=[]) for i in ids[:1]]
            message = SimpleNamespace(content=json.dumps(dict(moments=items)))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        items, _ = openai_analysis.analyse([dict(id=i, text=self.text) for i in range(2)], 'best', client=client, finish=True)
        self.assertEqual(calls, [[0, 1], [1]])  # the first reply skipped clip 1
        self.assertEqual(sorted(i['id'] for i in items), [0, 1])

    def test_plain_analysis_has_no_finishing_fields(self):
        item = dict(id=1, suitable=True, score=.9, evidence='the best pizza in town', reason='r', hook_quote='', context_quote='', emoji='')
        self.assertNotIn('title', openai_analysis._check(item, {1: dict(id=1, text=self.text)}))


if __name__ == '__main__':
    unittest.main()
