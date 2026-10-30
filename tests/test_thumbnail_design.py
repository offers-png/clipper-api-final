"""Designed thumbnails: frame scoring, text rules, layout (no face covered, face >= 40%) and API errors."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from clipforge_studios.backend import thumbnail_ai as thumb, thumbnail_design as design

FRONTAL = dict(box=[100, 100, 100, 130], landmarks=[[125, 150], [175, 150], [150, 180], [130, 205], [170, 205]], score=.9)


def synthetic(folder):
    """A 1280x720 frame with one person (head and shoulders) and its cutout."""
    frame = np.full((720, 1280, 3), (90, 120, 60), np.uint8)
    alpha = np.zeros((720, 1280), np.uint8)
    cv2.ellipse(alpha, (640, 260), (90, 120), 0, 0, 360, 255, -1)
    cv2.rectangle(alpha, (470, 370), (810, 720), 255, -1)
    frame[alpha > 0] = (60, 90, 200)  # BGR: a red person on green
    cv2.imwrite(str(folder / 'design-frame.png'), frame)
    cv2.imwrite(str(folder / 'design-alpha.png'), alpha)
    return dict(time=1.0, face=[550, 140, 180, 240], emotion='surprise', expressive=.9, facing=.9)


class ScoringTests(unittest.TestCase):
    def test_facing_prefers_a_frontal_head(self):
        turned = dict(FRONTAL, landmarks=[[125, 150], [175, 150], [168, 180], [130, 205], [170, 205]])
        self.assertGreater(design.facing(FRONTAL), .9)
        self.assertLess(design.facing(turned), design.facing(FRONTAL) / 2)
        self.assertEqual(design.facing(dict(FRONTAL, landmarks=[[125, 150], [175, 150], [150, 140], [130, 120], [170, 120]])), 0)

    def test_every_style_keeps_the_face_at_least_40_percent_tall(self):
        self.assertTrue(all(style['face'] >= .40 for style in design.STYLES.values()))
        self.assertEqual(set(design.STYLES), {'mrbeast', 'mkbhd', 'mystery'})


class TextTests(unittest.TestCase):
    def test_at_most_four_words_without_a_dangling_end(self):
        self.assertEqual(thumb.thumbnail_text('Build Agents For The Whole Team')[0], ['Build', 'Agents'])
        self.assertEqual(thumb.thumbnail_text('Why Does This Happen?')[0], ['Why', 'Does', 'This', 'Happen?'])

    def test_key_word_given_then_number_then_longest_key_word(self):
        self.assertEqual(thumb.thumbnail_text('Save Money With Grokbot', 'money'), (['Save', 'Money', 'With', 'Grokbot'], 1))
        self.assertEqual(thumb.thumbnail_text('I Built 47 Agents')[1], 2)
        self.assertEqual(thumb.thumbnail_text('Your Brain Forgets Everything')[1], 3)
        self.assertEqual(thumb.thumbnail_text('Grokbot Changes It', 'missing')[1], 0)


class RenderTests(unittest.TestCase):
    def test_each_style_renders_with_text_clear_of_the_face(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); frame = synthetic(folder)
            for style in design.STYLES:
                with self.subTest(style=style):
                    image = design.render(folder, frame, ['Build', 'AI', 'Agents', 'Fast'], 1, style)
                    self.assertEqual(image.size, (1280, 720))
                    pixels = np.asarray(image)
                    # The face region shows the person's colour, not text or background.
                    look = design.STYLES[style]; scale = look['face'] * 720 / frame['face'][3]
                    left = min(1280 * .97 - frame['face'][2] * scale, max(1280 * .47, 1280 * .70 - frame['face'][2] * scale / 2))
                    centre = pixels[int(720 * .08 + frame['face'][3] * scale / 2), int(left + frame['face'][2] * scale / 2)]
                    self.assertGreater(int(centre[0]), max(int(centre[1]), int(centre[2])) + 40)  # still the red person

    def test_text_that_would_cover_the_face_is_refused(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); frame = synthetic(folder)
            with patch.object(design, '_layout', return_value=(200, [['ENORMOUSLY', 'LONG']])):
                with self.assertRaisesRegex(ValueError, 'cover the face'):
                    design.render(folder, frame, ['Enormously', 'Long'], 0, 'mrbeast')


class DesignApiTests(unittest.TestCase):
    def setUp(self):
        host = FastAPI(); thumb.register(host); self.http = TestClient(host)
        self.job = dict(id='a' * 32, source_id='b' * 32, status='completed',
                        analysis=dict(concepts=[dict(title=t) for t in ('Build AI Agents', 'Grokbot Changes Everything', 'The Hidden AI Team')]))
        thumb.JOBS[self.job['id']] = self.job; self.addCleanup(thumb.JOBS.pop, self.job['id'], None)

    def post(self, style='mkbhd'):
        return self.http.post(f'/thumbnail-ai/jobs/{self.job["id"]}/design', json=dict(style=style))

    def test_unknown_style_is_rejected(self):
        self.assertEqual(self.post('vaporwave').status_code, 422)

    def test_missing_models_explain_how_to_prepare_them(self):
        with patch.object(design, 'status', return_value=dict(ready=False, reason='Missing local models')):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn('Missing local models', response.json()['detail'])

    def test_jobs_without_their_upload_must_be_analyzed_again(self):
        self.job.pop('source_id')
        with patch.object(design, 'status', return_value=dict(ready=True, reason=None)):
            self.assertEqual(self.post().status_code, 409)

    def test_no_usable_face_is_reported(self):
        source = dict(source='video.mp4', duration=10.0)
        with patch.object(design, 'status', return_value=dict(ready=True, reason=None)), \
                patch.object(thumb.studio, '_load_source', return_value=source), \
                patch.object(design, 'prepare', side_effect=ValueError('No clear face turned toward the camera was found')):
            response = self.post()
        self.assertEqual(response.status_code, 422)
        self.assertIn('No clear face', response.json()['detail'])

    def test_style_uses_its_concept_title_and_caches_the_frame(self):
        source = dict(source='video.mp4', duration=10.0)
        frame = dict(time=1.0, face=[0, 0, 1, 1])
        rendered = []

        class Image:
            def save(self, path, quality):rendered.append(Path(path).name)
        with tempfile.TemporaryDirectory() as root, patch.object(thumb, 'ROOT', Path(root)), \
                patch.object(design, 'status', return_value=dict(ready=True, reason=None)), \
                patch.object(thumb.studio, '_load_source', return_value=source), \
                patch.object(design, 'prepare', return_value=frame) as prepare, \
                patch.object(design, 'render', return_value=Image()) as render:
            (Path(root) / self.job['id']).mkdir()
            first, second = self.post('mkbhd').json(), self.post('mystery').json()
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual((first['title'], second['title']), ('Grokbot Changes Everything', 'The Hidden AI Team'))
        self.assertEqual(rendered, ['design_mkbhd.jpg', 'design_mystery.jpg'])
        self.assertEqual(render.call_args_list[0].args[2:], (['Grokbot', 'Changes', 'Everything'], 2, 'mkbhd'))


if __name__ == '__main__':
    unittest.main()
