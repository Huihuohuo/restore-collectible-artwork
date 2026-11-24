"""Known physical outlines, ambiguous edges, and opaque-host round trips."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image, ImageDraw

from fixture import create_fixture
from test_custom_and_adjustments import PREPARE, PIPELINE, JOB

sys.path.insert(0, str(Path(PREPARE.__file__).parent))
import inspect_geometry as INSPECT


def synthetic(radius, occlude=False):
    # Analytic distance to a rounded rectangle, independent of Pillow's mask.
    w, h, pad = 320, 500, 24
    y, x = np.mgrid[-pad:h+pad, -pad:w+pad]
    cx = np.clip(x, radius, w-1-radius)
    cy = np.clip(y, radius, h-1-radius)
    inside = ((x-cx)**2+(y-cy)**2 <= radius**2)
    inside &= (x >= 0) & (x < w) & (y >= 0) & (y < h)
    rgb = np.where(inside[..., None], [40, 95, 160], [212, 180, 120]).astype(np.uint8)
    if occlude:
        rgb[:pad+80, :pad+80] = [130, 130, 130]
    return Image.fromarray(rgb), (w, h), pad


class GeometryDiagnostics(unittest.TestCase):
    def test_separate_known_radii_and_straight_corners(self):
        for radius in (0, 18, 38):
            with self.subTest(radius=radius):
                im, size, pad = synthetic(radius)
                report, _ = INSPECT.fit_corners(im, size, pad, 60)
                self.assertIsNotNone(report['shared_radius_output_px'])
                self.assertLessEqual(abs(report['shared_radius_output_px']-radius), 2)
                self.assertEqual(len(report['supporting_corners']), 4)

    def test_occluded_corner_is_not_averaged_into_radius(self):
        im, size, pad = synthetic(32, occlude=True)
        report, _ = INSPECT.fit_corners(im, size, pad, 60)
        self.assertLessEqual(abs(report['shared_radius_output_px']-32), 2)
        self.assertNotIn('TL', report['supporting_corners'])

    def test_flat_low_contrast_photo_does_not_produce_a_default_radius(self):
        im = Image.new('RGB', (368, 548), '#888888')
        report, _ = INSPECT.fit_corners(im, (320, 500), 24, 60)
        self.assertIsNone(report['shared_radius_output_px'])

    def test_corner_disagreement_does_not_create_an_average_radius(self):
        im, size, pad = synthetic(18)
        other, _, _ = synthetic(48)
        im.paste(other.crop((0, 0, 150, 150)), (0, 0))
        report, _ = INSPECT.fit_corners(im, size, pad, 65)
        self.assertLessEqual(abs(report['shared_radius_output_px']-18), 2)
        self.assertEqual(report['disagreeing_reliable_corners'], ['TL'])

    def test_edge_search_recovers_translated_seed_without_fixed_inset(self):
        im, size, pad = synthetic(22)
        truth = np.array([[pad,pad], [pad+319,pad], [pad+319,pad+499], [pad,pad+499]], float)
        quad, report = INSPECT.candidate_edges(im, truth+[3, -4], 10)
        self.assertLess(float(np.max(np.abs(quad-truth))), 1.2)
        self.assertTrue(all(r['support_fraction'] > .9 for r in report))

    def test_pending_diagnostic_is_not_silently_used_as_a_final_crop(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            config = create_fixture(folder, PIPELINE)
            geometry = json.loads((folder/'geometry.json').read_text())
            geometry['geometry_review'] = 'pending'
            (folder/'candidate.json').write_text(json.dumps(geometry))
            with self.assertRaisesRegex(ValueError, 'pending review'):
                PREPARE.prepare(SimpleNamespace(input=folder/'source.png', geometry=folder/'candidate.json',
                                                out_dir=folder/'new', allow_legacy_geometry=False))
            self.assertFalse((folder/'new').exists())


class OpaqueRoundedHost(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config = create_fixture(self.folder, PIPELINE, {
            'corner_radius_px': 25, 'generation_layout': 'full_canvas', 'edit_gutter_px': 0})
        data = json.loads(self.config.read_text())
        data.pop('generation_layout')  # Geometry carries the selected host contract.
        data['crop'] = 'auto'
        data.pop('baseline')  # Corner extension may affect only antialiased boundary RGB.
        data['expected_sha256'].pop('baseline')
        self.config.write_text(json.dumps(data))

    def tearDown(self):
        self.temp.cleanup()

    def test_opaque_generation_restores_exact_rounded_alpha_and_preserves_interior(self):
        result = PIPELINE.run(self.config, self.folder/'result', new_generated=self.folder/'generated.png')
        with Image.open(result['output']) as out, Image.open(self.folder/'baseline.png') as ref:
            actual, expected = np.array(out), np.array(ref)
        np.testing.assert_array_equal(actual[:, :, 3], expected[:, :, 3])
        opaque = expected[:, :, 3] == 255
        np.testing.assert_array_equal(actual[:, :, :3][opaque], expected[:, :, :3][opaque])
        self.assertEqual(result['crop'], [0, 0, 540, 850])
        self.assertFalse(result['generation']['transparent_background'])

    def test_start_accepts_prepared_opaque_input_for_a_rounded_reference(self):
        tool = self.folder/'tool'
        tool.mkdir()
        ready = JOB.start(SimpleNamespace(config=self.config, prompt=self.folder/'prompt.txt',
            edit_input=self.folder/'prepared/edit-input.png', tool_dir=tool, job_dir=self.folder/'job'))
        self.assertFalse(ready['transparent_background'])
        with Image.open(ready['edit_input']) as picture:
            self.assertEqual(picture.convert('RGBA').getchannel('A').getextrema(), (255,255))

    def test_transparent_background_holes_and_wrong_ratio_remain_rejected(self):
        for kind in ('hole', 'ratio'):
            with self.subTest(kind=kind):
                with Image.open(self.folder/'generated.png') as picture:
                    picture = picture.convert('RGBA')
                if kind == 'hole':
                    ImageDraw.Draw(picture).rectangle((70, 90, 130, 150), fill=(0,0,0,0))
                else:
                    picture = picture.resize((700, 850))
                path = self.folder/f'{kind}.png'
                picture.save(path)
                with self.assertRaises(ValueError):
                    PIPELINE.run(self.config, self.folder/kind, new_generated=path)
                self.assertFalse((self.folder/kind/'digital-artwork.png').exists())

    def test_opaque_image_is_not_auto_accepted_by_transparent_object_contract(self):
        data = json.loads(self.config.read_text())
        data['generation_layout'] = 'transparent_object'
        self.config.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'generation_layout: "full_canvas"'):
            PIPELINE.run(self.config, self.folder/'ambiguous', new_generated=self.folder/'generated.png')


if __name__ == '__main__':
    unittest.main()
