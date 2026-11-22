"""Guard real local replay failures; no generation, network or app changes."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
import time

from PIL import Image

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('artwork_local', ROOT/'skills/restore-collectible-artwork/scripts/process-artwork-local.py')
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)
from fixture import create_fixture
FIXTURE_TEMP = tempfile.TemporaryDirectory(prefix='artwork-synthetic-')
FIXTURE = create_fixture(Path(FIXTURE_TEMP.name), PIPELINE)
JOB_SPEC = importlib.util.spec_from_file_location('artwork_job', ROOT/'skills/restore-collectible-artwork/scripts/artwork-job.py')
JOB = importlib.util.module_from_spec(JOB_SPEC)
JOB_SPEC.loader.exec_module(JOB)


class LocalArtworkGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='artwork-guard-')
        self.folder = Path(self.temporary.name)
        data, paths = PIPELINE.read_config(FIXTURE)
        self.config = copy.deepcopy(data)
        self.config.update({key: str(path) for key, path in paths.items() if key != 'config'})
        self.protected = {key: PIPELINE.digest(path) for key, path in paths.items()}
        self.protected_paths = paths

    def tearDown(self):
        for key, path in self.protected_paths.items():
            self.assertEqual(PIPELINE.digest(path), self.protected[key])
        self.temporary.cleanup()

    def config_file(self):
        path = self.folder/'replay.json'
        path.write_text(json.dumps(self.config))
        return path

    def change_geometry(self, change):
        data = json.loads(Path(self.config['geometry']).read_text())
        change(data)
        path = self.folder/'geometry.json'
        path.write_text(json.dumps(data))
        self.config['geometry'] = str(path)
        self.config['expected_sha256']['geometry'] = PIPELINE.digest(path)

    def test_relative_paths_resolve_beside_config(self):
        _, paths = PIPELINE.read_config(FIXTURE)
        self.assertEqual(paths['generated'], (FIXTURE.parent/'generated.png').resolve())

    def test_existing_output_is_not_overwritten(self):
        output = self.folder/'existing'
        output.mkdir()
        (output/'marker').write_text('preserve')
        with self.assertRaises(FileExistsError):
            PIPELINE.run(self.config_file(), output)
        self.assertEqual((output/'marker').read_text(), 'preserve')

    def test_missing_format_does_not_choose_a_default(self):
        self.change_geometry(lambda spec: spec.pop('format_id'))
        with self.assertRaisesRegex(ValueError, 'explicit format_id'):
            PIPELINE.run(self.config_file(), self.folder/'out')
        self.assertFalse((self.folder/'out').exists())

    def test_changed_known_quad_cannot_reuse_stale_reference(self):
        self.change_geometry(lambda spec: spec['quad'][0].__setitem__(0, spec['quad'][0][0]+1))
        with self.assertRaisesRegex(ValueError, 'Cached reference geometry differs'):
            PIPELINE.run(self.config_file(), self.folder/'out')

    def test_input_fingerprint_change_stops_before_processing(self):
        self.config['expected_sha256']['generated'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'fingerprint differs'):
            PIPELINE.run(self.config_file(), self.folder/'out')
        self.assertFalse((self.folder/'out').exists())

    def test_bad_body_crop_is_not_stretched_to_fit(self):
        self.config['crop'] = [0, 0, 100, 100]
        output = self.folder/'out'
        with self.assertRaisesRegex(ValueError, 'aspect differs'):
            PIPELINE.run(self.config_file(), output)
        self.assertEqual(json.loads((output/'run.json').read_text())['status'], 'failed')
        self.assertFalse((output/'digital-artwork.png').exists())

    def test_one_changed_baseline_pixel_refuses_final_export(self):
        with Image.open(self.config['baseline']) as source:
            altered = source.convert('RGBA')
        pixel = altered.getpixel((270, 425))
        altered.putpixel((270, 425), ((pixel[0]+1) % 256, *pixel[1:]))
        baseline = self.folder/'changed-baseline.png'
        altered.save(baseline)
        self.config['baseline'] = str(baseline)
        self.config['expected_sha256']['baseline'] = PIPELINE.digest(baseline)
        output = self.folder/'out'
        with self.assertRaisesRegex(ValueError, 'Quality check failed'):
            PIPELINE.run(self.config_file(), output)
        report = json.loads((output/'run.json').read_text())
        self.assertEqual(report['quality']['approved_baseline']['changed_pixels'], 1)
        self.assertFalse((output/'digital-artwork.png').exists())

    def test_featureless_artwork_keeps_alignment_failure(self):
        generated = self.folder/'featureless.png'
        with Image.open(self.config['generated']) as source:
            Image.new('RGBA', source.size, (100, 100, 100, 255)).save(generated)
        self.config['generated'] = str(generated)
        self.config['expected_sha256']['generated'] = PIPELINE.digest(generated)
        output = self.folder/'out'
        with self.assertRaisesRegex(ValueError, 'Too few reliable matches'):
            PIPELINE.run(self.config_file(), output)
        self.assertEqual(json.loads((output/'alignment/report.json').read_text())['status'], 'needs_manual_alignment')
        self.assertFalse((output/'digital-artwork.png').exists())

    def test_auto_body_matches_synthetic_padding(self):
        self.assertEqual(PIPELINE.detect_body(FIXTURE.parent/'generated.png', (540, 850))['crop'], [30, 30, 570, 880])

    def test_first_generation_needs_no_previous_generation_or_baseline(self):
        generated = self.config.pop('generated')
        self.config.pop('baseline')
        self.config['expected_sha256'].pop('generated')
        self.config['expected_sha256'].pop('baseline')
        result = PIPELINE.run(self.config_file(), self.folder/'fresh', new_generated=generated)
        self.assertTrue(result['quality']['passed'])
        self.assertNotIn('approved_baseline', result['quality'])

    def test_first_generation_job_needs_no_previous_artwork(self):
        import shutil
        generated = self.config.pop('generated')
        self.config.pop('baseline')
        self.config['expected_sha256'].pop('generated')
        self.config['expected_sha256'].pop('baseline')
        tool_dir, job_dir = self.start_job()
        started = time.time()
        shutil.copyfile(generated, tool_dir/'new.png')
        result = JOB.finish(SimpleNamespace(job_dir=job_dir, tool_started_unix=started, tool_ended_unix=time.time()))
        self.assertTrue(Path(result['output']).is_file())

    def test_shifted_content_is_corrected_without_changing_reference_alpha(self):
        import numpy as np
        with Image.open(self.config['reference']) as source:
            reference = source.convert('RGBA')
        values = np.array(reference)
        padded = np.pad(values, ((6, 0), (4, 0), (0, 0)), mode='edge')
        shifted = Image.fromarray(padded[:values.shape[0], :values.shape[1]])
        moving = self.folder/'shifted.png'
        shifted.save(moving)
        align = PIPELINE.load_helper('align_artwork')
        align.run(SimpleNamespace(reference=self.config['reference'], artwork=moving,
                                  out_dir=self.folder/'shift', mode='auto',
                                  max_edge_fraction=.015, diagnostics=False))
        report = json.loads((self.folder/'shift/report.json').read_text())
        self.assertLess(report['after_held_out']['p90_px'], report['before']['p90_px'])
        with Image.open(self.folder/'shift/aligned.png') as aligned:
            self.assertTrue(np.array_equal(np.array(aligned)[:, :, 3], values[:, :, 3]))

    def test_sparse_alpha_fringe_is_not_the_card_body(self):
        from PIL import ImageDraw
        image = Image.new('RGBA', (200, 260))
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 35, 159, 223), fill=(80, 80, 80, 255))
        draw.line((0, 0, 199, 0), fill=(255, 255, 255, 255))
        path = self.folder/'fringe.png'
        image.save(path)
        self.assertEqual(PIPELINE.detect_body(path, (54, 85))['crop'], [40, 35, 160, 224])

    def test_opaque_card_background_and_wrong_aspect_refuse_export(self):
        for color, rect, error in [
            ((80, 80, 80, 255), None, 'transparent exterior'),
            ((0, 0, 0, 0), (30, 30, 229, 229), 'shape/aspect'),
        ]:
            from PIL import ImageDraw
            path = self.folder/f'bad-{error[:3]}.png'
            image = Image.new('RGBA', (260, 260), color)
            if rect:
                ImageDraw.Draw(image).rectangle(rect, fill=(80, 80, 80, 255))
            image.save(path)
            output = self.folder/f'out-{error[:3]}'
            with self.assertRaisesRegex(ValueError, error):
                PIPELINE.run(self.config_file(), output, new_generated=path)
            self.assertFalse((output/'digital-artwork.png').exists())
            self.assertEqual(json.loads((output/'run.json').read_text())['status'], 'failed')

    def test_two_card_bodies_are_ambiguous(self):
        from PIL import ImageDraw
        image = Image.new('RGBA', (300, 300))
        draw = ImageDraw.Draw(image)
        for rect in [(20, 20, 120, 280), (180, 20, 280, 280)]:
            draw.rectangle(rect, fill=(80, 80, 80, 255))
        path = self.folder/'two.png'
        image.save(path)
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            PIPELINE.detect_body(path, (54, 85))

    def test_new_generation_ignores_old_generation_hash_but_retains_source_guard(self):
        self.config['expected_sha256']['generated'] = '0'*64
        self.config['expected_sha256']['baseline'] = '0'*64
        self.config['expected_sha256']['source'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'fingerprint differs: source'):
            PIPELINE.run(self.config_file(), self.folder/'bad', new_generated=self.config['generated'])
        self.config['expected_sha256']['source'] = PIPELINE.digest(self.config['source'])
        report = PIPELINE.run(self.config_file(), self.folder/'good', new_generated=self.config['generated'])
        self.assertNotIn('approved_baseline', report['quality'])
        self.assertEqual(report['status'], 'local_checks_passed_requires_visual_review')
        self.assertFalse(report['scope']['generated_image_reused'])
        check = PIPELINE.verify_pixels(report['output'], self.config['reference'], (540, 850), self.config['baseline'])
        self.assertTrue(check['passed'])

    def start_job(self):
        tool_dir = self.folder/'tool-output'
        tool_dir.mkdir()
        job_dir = self.folder/'job'
        JOB.start(SimpleNamespace(config=self.config_file(), job_dir=job_dir, tool_dir=tool_dir,
                                 prompt=FIXTURE.parent/'prompt.txt',
                                 edit_input=FIXTURE.parent/'prepared/edit-input.png'))
        return tool_dir, job_dir

    def test_job_does_not_guess_multiple_new_images(self):
        import shutil
        tool_dir, job_dir = self.start_job()
        for name in ['one.png', 'two.png']:
            shutil.copyfile(self.config['generated'], tool_dir/name)
        with self.assertRaisesRegex(ValueError, 'exactly one new tool PNG; found 2'):
            JOB.finish(SimpleNamespace(job_dir=job_dir, tool_started_unix=time.time()-.1, tool_ended_unix=time.time()))
        self.assertFalse((job_dir/'result/digital-artwork.png').exists())
        self.assertEqual(json.loads((job_dir/'job.json').read_text())['status'], 'failed')

    def test_job_without_new_image_stops(self):
        _, job_dir = self.start_job()
        with self.assertRaisesRegex(ValueError, 'exactly one new tool PNG; found 0'):
            JOB.finish(SimpleNamespace(job_dir=job_dir, tool_started_unix=time.time()-.001, tool_ended_unix=time.time()))
        self.assertFalse((job_dir/'result/digital-artwork.png').exists())

    def test_job_detects_protected_prompt_change_before_processing(self):
        import shutil
        prompt = self.folder/'prompt.txt'
        prompt.write_text('fixed prompt')
        tool_dir = self.folder/'tool-output'
        tool_dir.mkdir()
        job_dir = self.folder/'job'
        JOB.start(SimpleNamespace(config=self.config_file(), job_dir=job_dir, tool_dir=tool_dir,
                                 prompt=prompt, edit_input=FIXTURE.parent/'prepared/edit-input.png'))
        prompt.write_text('changed prompt')
        shutil.copyfile(self.config['generated'], tool_dir/'new.png')
        with self.assertRaisesRegex(ValueError, 'Protected input changed'):
            JOB.finish(SimpleNamespace(job_dir=job_dir, tool_started_unix=time.time()-.1, tool_ended_unix=time.time()))
        self.assertFalse((job_dir/'result/digital-artwork.png').exists())

    def test_job_completes_single_handoff_with_unchanged_artwork(self):
        import shutil
        tool_dir, job_dir = self.start_job()
        tool_start = time.time()
        shutil.copyfile(self.config['generated'], tool_dir/'new.png')
        result = JOB.finish(SimpleNamespace(job_dir=job_dir, tool_started_unix=tool_start, tool_ended_unix=time.time()))
        self.assertTrue(Path(result['comparison']).is_file())
        check = PIPELINE.verify_pixels(result['output'], self.config['reference'], (540, 850), self.config['baseline'])
        self.assertTrue(check['passed'])
        with self.assertRaisesRegex(ValueError, 'already finished'):
            JOB.finish(SimpleNamespace(job_dir=job_dir, tool_started_unix=tool_start, tool_ended_unix=time.time()))


if __name__ == '__main__':
    unittest.main()
