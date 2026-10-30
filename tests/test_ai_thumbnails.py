"""AI thumbnails: styles, bundled previews, prices, prompts, face reference vs scene, and the confirm step."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from clipforge_studios.backend import ai_thumbnails, thumbnail_ai

PREVIEWS = Path(__file__).resolve().parents[2] / 'review_frontend' / 'public' / 'studios' / 'thumbnail-styles'
NAMES = ["Aerial view", "Collector's tin", "'80s flashback", "Caricature", "Sketch", "Stickers", "Oktoberfest throwback",
         "Photo booth", "Y2K digicam", "Anime", "70s portrait", "Impressionist painting", "Pin collection", "Underwater",
         "3D avatar", "Acrylic painting", "Felted miniature", "Tarot card", "Statue", "Claymation world", "Model kit",
         "16-bit arcade", "Handwritten style", "Scribble", "Your bobblehead", "Mini me", "Cross-section", "Wanderlust",
         "Nighttime flash", "Blueprint poster", "Comic", "Anime comic", "Fantasy newspaper", "Film strip", "Hyperreal wallpaper"]


def png():
    from PIL import Image
    out = io.BytesIO();Image.new('RGB', (1280, 720), 'teal').save(out, 'PNG');return base64.b64encode(out.getvalue()).decode()


class FakeImages:
    def __init__(self):self.calls = []
    def _reply(self):
        usage = SimpleNamespace(input_tokens_details=SimpleNamespace(image_tokens=1508, text_tokens=200), output_tokens=947)
        return SimpleNamespace(data=[SimpleNamespace(b64_json=png())], usage=usage)
    def edit(self, **options):self.calls.append(('edit', options));return self._reply()
    def generate(self, **options):self.calls.append(('generate', options));return self._reply()


class StyleTests(unittest.TestCase):
    def test_exactly_the_requested_styles_in_order(self):
        self.assertEqual(list(ai_thumbnails.STYLES), NAMES)

    def test_every_style_has_a_bundled_preview_picture(self):
        slugs = [ai_thumbnails.slug(n) for n in NAMES]
        self.assertEqual(len(set(slugs)), 35)
        self.assertEqual(ai_thumbnails.slug("Collector's tin"), 'collectors-tin')
        missing = [s for s in slugs if not (PREVIEWS / f'{s}.jpg').is_file()]
        self.assertEqual(missing, [])

    def test_prices_per_quality(self):
        prices = {q: round(ai_thumbnails.price(q), 3) for q in ai_thumbnails.QUALITIES}
        self.assertEqual(prices, {'fast': 0.016, 'standard': 0.042, 'best': 0.127})
        self.assertLess(ai_thumbnails.price('standard', reference=False), ai_thumbnails.price('standard'))

    def test_prompt_keeps_the_top_clear_and_asks_for_no_text(self):
        with_face = ai_thumbnails.prompt('Anime', dict(has_person=True, scene='A man talks about AI tools.'))
        self.assertIn('reference photo', with_face);self.assertIn('Do not draw any words', with_face)
        self.assertIn('fills the whole frame', with_face);self.assertIn('right side', with_face);self.assertNotIn('top third', with_face)
        scene = ai_thumbnails.prompt('Comic', dict(has_person=False, scene='Robots sorting email.'))
        self.assertNotIn('reference photo', scene);self.assertIn('Robots sorting email.', scene)


class GenerateTests(unittest.TestCase):
    def test_face_frame_goes_along_as_a_reference(self):
        images = FakeImages();client = SimpleNamespace(images=images)
        data, usd = ai_thumbnails.generate('Anime', dict(has_person=True, scene='x'), 'standard', b'jpeg', client)
        kind, options = images.calls[0]
        self.assertEqual((kind, options['model'], options['size'], options['quality']), ('edit', 'gpt-image-2', '1280x720', 'medium'))
        self.assertAlmostEqual(usd, (1508 * 8 + 200 * 5 + 947 * 30) / 1e6)

    def test_no_face_draws_a_scene_from_the_topic(self):
        images = FakeImages();client = SimpleNamespace(images=images)
        ai_thumbnails.generate('Comic', dict(has_person=False, scene='x'), 'fast', b'jpeg', client)
        self.assertEqual(images.calls[0][0], 'generate');self.assertEqual(images.calls[0][1]['quality'], 'low')

    def test_batch_makes_one_image_per_style(self):
        images = FakeImages()
        brief = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(dict(has_person=False, person='', scene='AI tools'))))], usage=None)
        client = SimpleNamespace(images=images, chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: brief)))
        batch = dict(id='b' * 32, job_id='a' * 32, styles=['Anime', 'Comic', 'Sketch'], quality='fast', items=[])
        with tempfile.TemporaryDirectory() as folder:
            ai_thumbnails.run_batch(batch, dict(topic='AI tools', concepts=[]), None, folder, client, 'gpt-4o-mini')
            self.assertEqual(batch['status'], 'completed')
            self.assertEqual([i['slug'] for i in batch['items']], ['anime', 'comic', 'sketch'])
            self.assertEqual({i['title_side'] for i in batch['items']}, {'left'})  # no face in a plain picture: title on the left
            self.assertTrue(all((Path(folder) / f'ai_{"b" * 32}_{s}.jpg').is_file() for s in ('anime', 'comic', 'sketch')))
        self.assertEqual(len(images.calls), 3)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root = Path(temp.name);self.job_id = 'c' * 32
        (root / (self.job_id + '.json')).write_text(json.dumps(dict(id=self.job_id, status='completed', analysis=dict(topic='AI', concepts=[]))), encoding='utf-8')
        for patcher in (patch.object(thumbnail_ai, 'ROOT', root), patch.object(thumbnail_ai.moment_picker, 'enabled', return_value=True),
                        patch.object(thumbnail_ai, 'AI_BATCHES', {})):
            patcher.start();self.addCleanup(patcher.stop)
        app = FastAPI();thumbnail_ai.register(app);self.http = TestClient(app)

    def test_styles_list_previews_and_prices(self):
        data = self.http.get('/thumbnail-ai/ai-styles').json()
        self.assertEqual([s['name'] for s in data['styles']], NAMES)
        self.assertEqual(data['styles'][2]['preview'], '/studios/thumbnail-styles/80s-flashback.jpg')
        self.assertEqual([q['label'] for q in data['qualities']], ['Fast', 'Standard', 'Best'])

    def test_generation_needs_the_confirmed_total(self):
        url = f'/thumbnail-ai/jobs/{self.job_id}/ai-thumbnails'
        wrong = self.http.post(url, json=dict(styles=['Anime', 'Comic'], quality='standard', confirmed_total_usd=0.01))
        self.assertEqual(wrong.status_code, 409);self.assertIn('$0.084', wrong.json()['detail'])
        self.assertEqual(self.http.post(url, json=dict(styles=['Nope'], quality='fast', confirmed_total_usd=0.016)).status_code, 422)
        too_many = self.http.post(url, json=dict(styles=NAMES[:7], quality='fast', confirmed_total_usd=0.112))
        self.assertEqual(too_many.status_code, 422)
        with patch.object(thumbnail_ai.AI_POOL, 'submit') as submit:
            ok = self.http.post(url, json=dict(styles=['Anime', "'80s flashback", 'Comic'], quality='standard', confirmed_total_usd=0.126))
        self.assertEqual(ok.status_code, 202);submit.assert_called_once()
        self.assertEqual(self.http.get(f'{url}/{ok.json()["id"]}').json()['styles'], ['Anime', "'80s flashback", 'Comic'])

    def test_generation_counts_toward_the_daily_allowance(self):
        from clipforge_studios.access import analysis
        self.assertTrue(analysis(f'/thumbnail-ai/jobs/{self.job_id}/ai-thumbnails'))
        self.assertFalse(analysis(f'/thumbnail-ai/jobs/{self.job_id}/ai-thumbnails/{"b" * 32}'))


if __name__ == '__main__':
    unittest.main()
