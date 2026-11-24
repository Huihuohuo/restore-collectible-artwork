"""Exercise output routing and reject missing printed backgrounds; no model calls."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image, ImageDraw

from fixture import create_fixture
from test_custom_and_adjustments import PREPARE, PIPELINE, JOB


class PictureBackgrounds(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config = create_fixture(self.folder, PIPELINE, {
            'format_id': 'custom', 'object_type': 'photo print', 'target': 'whole_object',
            'dimensions': {'width': 100, 'height': 150, 'unit': 'mm'},
            'output_size': [540, 810], 'edit_gutter_px': 0,
        })
        data = json.loads(self.config.read_text())
        data.pop('generation_layout')  # Exercise the default inferred from the reference alpha.
        self.config.write_text(json.dumps(data))

    def tearDown(self):
        self.temp.cleanup()

    def test_white_and_dark_backgrounds_use_the_same_silhouette_routing(self):
        geometry = json.loads((self.folder/'geometry.json').read_text())
        geometry.pop('edit_gutter_px')
        for color in ('white', '#080b10'):
            for radius in (0, 15):
                with self.subTest(color=color, radius=radius):
                    name = f'{color.replace("#", "")}-{radius}'
                    source = self.folder/f'{name}.png'
                    Image.new('RGB', tuple(geometry['source_size']), color).save(source)
                    spec = self.folder/f'{name}.json'
                    spec.write_text(json.dumps({**geometry, 'corner_radius_px': radius}))
                    output = self.folder/name
                    with contextlib.redirect_stdout(io.StringIO()):
                        PREPARE.prepare(SimpleNamespace(input=source, geometry=spec, out_dir=output,
                                                        allow_legacy_geometry=False))
                    report = json.loads((output/'report.json').read_text())
                    self.assertEqual(report['generation']['transparent_background'], bool(radius))
                    with Image.open(output/'edit-input.png') as edit:
                        self.assertEqual(edit.size, (604, 874) if radius else (540, 810))
                        self.assertEqual(edit.getpixel((edit.width//2, edit.height//2)),
                                         (255, 255, 255, 255) if color == 'white' else (8, 11, 16, 255))

    def test_whole_rectangular_picture_runs_without_manual_crop_and_preserves_pixels(self):
        result = PIPELINE.run(self.config, self.folder/'result', auto_crop=True)
        self.assertEqual(result['generation']['layout'], 'full_canvas')
        self.assertEqual(result['crop'], [0, 0, 540, 810])
        self.assertTrue(result['quality']['approved_baseline']['rgba_pixel_exact'])
        self.assertFalse(result['delivery']['adjustments_applied'])

    def test_background_cutout_or_translucent_canvas_never_exports_a_final(self):
        for kind in ('cutout', 'translucent'):
            with self.subTest(kind=kind), Image.open(self.folder/'generated.png') as source:
                draft = source.convert('RGBA')
                if kind == 'cutout':
                    ImageDraw.Draw(draft).rectangle((40, 40, 180, 220), fill=(0, 0, 0, 0))
                else:
                    draft.putalpha(254)
                draft.save(self.folder/f'{kind}.png')
                output = self.folder/f'{kind}-result'
                with self.assertRaisesRegex(ValueError, 'completely opaque'):
                    PIPELINE.run(self.config, output, new_generated=self.folder/f'{kind}.png')
                self.assertFalse((output/'digital-artwork.png').exists())

    def test_normalization_cannot_hide_missing_interior_using_reference_alpha(self):
        with Image.open(self.folder/'generated.png') as source:
            draft = source.convert('RGBA')
        ImageDraw.Draw(draft).rectangle((40, 40, 180, 220), fill=(255, 255, 255, 0))
        draft.save(self.folder/'missing-background.png')
        with self.assertRaisesRegex(ValueError, 'transparent interior'):
            PREPARE.normalize(SimpleNamespace(input=self.folder/'missing-background.png',
                reference=self.folder/'prepared/reference.png', crop=[0, 0, 540, 810],
                out_dir=self.folder/'normalized'))
        self.assertFalse((self.folder/'normalized').exists())

    def test_full_canvas_rejects_wrong_aspect_and_hidden_cropping(self):
        Image.new('RGB', (640, 810), 'black').save(self.folder/'wrong-ratio.png')
        with self.assertRaisesRegex(ValueError, 'aspect differs'):
            PIPELINE.run(self.config, self.folder/'wrong-result', new_generated=self.folder/'wrong-ratio.png')
        data = json.loads(self.config.read_text())
        data['crop'] = [1, 0, 539, 810]
        self.config.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'retain the entire'):
            PIPELINE.run(self.config, self.folder/'cropped-result')
        self.assertFalse((self.folder/'cropped-result/digital-artwork.png').exists())

    def test_full_canvas_job_handoff_uses_opaque_output_and_records_actual_prompt(self):
        tool = self.folder/'tool'
        tool.mkdir()
        job = self.folder/'job'
        source_prompt = (self.folder/'prompt.txt').read_bytes()
        ready = JOB.start(SimpleNamespace(config=self.config, prompt=self.folder/'prompt.txt',
            edit_input=self.folder/'prepared/edit-input.png', tool_dir=tool, job_dir=job))
        self.assertFalse(ready['transparent_background'])
        self.assertEqual(ready['generation_layout'], 'full_canvas')
        self.assertEqual((job/'tool-prompt.txt').read_text(), ready['prompt'])
        self.assertEqual((self.folder/'prompt.txt').read_bytes(), source_prompt)
        began = time.time()
        shutil.copyfile(self.folder/'generated.png', tool/'new.png')
        result = JOB.finish(SimpleNamespace(job_dir=job, tool_started_unix=began, tool_ended_unix=time.time()))
        with Image.open(result['output']) as actual, Image.open(self.folder/'baseline.png') as expected:
            np.testing.assert_array_equal(np.array(actual), np.array(expected))

    def test_wrong_padded_edit_input_is_rejected_before_generation(self):
        with Image.open(self.folder/'generated.png') as source:
            padded = Image.new('RGBA', (600, 870))
            padded.paste(source, (30, 30))
        padded.save(self.folder/'padded.png')
        tool = self.folder/'tool'
        tool.mkdir()
        with self.assertRaisesRegex(ValueError, 'use prepared/reference.png'):
            JOB.start(SimpleNamespace(config=self.config, prompt=self.folder/'prompt.txt',
                edit_input=self.folder/'padded.png', tool_dir=tool, job_dir=self.folder/'job'))
        self.assertFalse((self.folder/'job').exists())

    def test_round_outline_cannot_request_full_canvas_and_legacy_layout_still_checks_holes(self):
        reference = Image.new('RGBA', (200, 300), 'white')
        reference.putpixel((0, 0), (255, 255, 255, 0))
        with self.assertRaisesRegex(ValueError, 'opaque rectangular reference'):
            PREPARE.generation_contract(reference, 'full_canvas')
        data = json.loads(self.config.read_text())
        data['generation_layout'] = 'transparent_object'
        self.config.write_text(json.dumps(data))
        with Image.open(self.folder/'generated.png') as source:
            draft = Image.new('RGBA', (600, 870))
            draft.paste(source, (30, 30))
        ImageDraw.Draw(draft).rectangle((80, 80, 100, 100), fill=(8, 8, 8, 0))
        draft.save(self.folder/'legacy-hole.png')
        with self.assertRaisesRegex(ValueError, 'transparent interior'):
            PIPELINE.run(self.config, self.folder/'legacy-result', new_generated=self.folder/'legacy-hole.png')
        self.assertFalse((self.folder/'legacy-result/digital-artwork.png').exists())


if __name__ == '__main__':
    unittest.main()
