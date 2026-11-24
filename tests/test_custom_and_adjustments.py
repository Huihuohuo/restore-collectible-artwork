import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image
from fixture import create_fixture

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT/'skills/restore-collectible-artwork'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SKILL/'scripts'/f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


PREPARE = load('prepare_artwork')
PIPELINE = load('process-artwork-local')
JOB = load('artwork-job')
COLOR = load('adjust_artwork')


class CustomFormats(unittest.TestCase):
    def test_missing_intake_is_rejected_before_preparing(self):
        complete = {'format_id': 'custom', 'object_type': 'postcard', 'target': 'whole_object',
                    'dimensions': {'width': 148, 'height': 100, 'unit': 'mm'}}
        for key in ('object_type', 'dimensions', 'target'):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                spec = {k: v for k, v in complete.items() if k != key}
                (folder/'geometry.json').write_text(json.dumps(spec))
                with self.assertRaises(ValueError):
                    PREPARE.prepare(SimpleNamespace(input=folder/'not-read.png', geometry=folder/'geometry.json',
                                                    out_dir=folder/'prepared', allow_legacy_geometry=False))
                self.assertFalse((folder/'prepared').exists())

    def test_common_preset_cannot_silently_change_user_dimensions(self):
        spec = {'format_id': 'photocard-standard', 'object_type': 'card',
                'dimensions': {'width': 54, 'height': 86, 'unit': 'mm'}}
        with self.assertRaisesRegex(ValueError, 'differ from the preset'):
            PREPARE.output_size(spec)
        size, metadata = PREPARE.output_size({**spec, 'format_id': 'custom', 'target': 'whole_object'})
        self.assertEqual(size[0]*86, size[1]*54)
        self.assertEqual(metadata['dimensions']['height'], 86)

    def test_custom_units_orientation_and_pixel_rounding(self):
        for width, height, unit in [(148, 100, 'mm'), (14.8, 10, 'cm'), (6, 4, 'in'), (900, 600, 'px'), (54.123, 85, 'mm')]:
            size, meta = PREPARE.output_size({'format_id': 'custom', 'object_type': 'postcard', 'target': 'whole_object',
                                            'dimensions': {'width': width, 'height': height, 'unit': unit}})
            self.assertLessEqual(meta['short_edge_rounding_px'], .50000001)
            self.assertEqual(size[0] > size[1], width > height)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            PREPARE.output_size({'format_id': 'custom', 'object_type': 'poster', 'target': 'whole_object',
                                 'dimensions': {'width': 100, 'height': 150, 'unit': 'mm'}, 'output_size': [1000, 1600]})

    def test_invalid_dimensions_and_resolution_are_rejected(self):
        base = {'format_id': 'custom', 'object_type': 'poster', 'target': 'whole_object'}
        for width, height, unit in [(0, 10, 'mm'), (True, 10, 'mm'), (float('nan'), 10, 'mm'), (10, 20, 'unknown'), (1.5, 200, 'px')]:
            with self.subTest(width=width, unit=unit), self.assertRaises(ValueError):
                PREPARE.output_size({**base, 'dimensions': {'width': width, 'height': height, 'unit': unit}})
        with self.assertRaisesRegex(ValueError, 'Choose only one'):
            PREPARE.output_size({**base, 'dimensions': {'width': 10, 'height': 15, 'unit': 'cm'}, 'output_size': [600, 900], 'long_edge_px': 1000})

    def test_custom_initial_pipeline_and_job_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            config = create_fixture(folder, PIPELINE, {'format_id': 'custom', 'object_type': 'poster',
                'dimensions': {'width': 10, 'height': 15, 'unit': 'cm'}, 'target': 'whole_object', 'output_size': [540, 810]})
            result = PIPELINE.run(config, folder/'result', auto_crop=True)
            self.assertEqual(result['quality']['size'], [540, 810])
            self.assertTrue(result['quality']['approved_baseline']['rgba_pixel_exact'])
            self.assertFalse(result['delivery']['adjustments_applied'])
            self.assertEqual(result['delivery']['master'], result['output'])
            (folder/'tool').mkdir()
            ready = JOB.start(SimpleNamespace(config=config, prompt=folder/'prompt.txt', edit_input=folder/'prepared/edit-input.png',
                                             tool_dir=folder/'tool', job_dir=folder/'job'))
            self.assertTrue(ready['transparent_background'])

    def test_custom_picture_only_and_resolved_long_edge_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            # Use the explicit long-edge resolution path and later reuse its report.
            config = create_fixture(folder, PIPELINE, {'format_id': 'custom', 'object_type': 'picture',
                'dimensions': {'width': 54, 'height': 85, 'unit': 'mm'}, 'target': 'picture_only'})
            geometry = json.loads((folder/'geometry.json').read_text())
            geometry.pop('output_size'); geometry['long_edge_px'] = 850
            (folder/'geometry-long.json').write_text(json.dumps(geometry))
            with contextlib.redirect_stdout(io.StringIO()):
                PREPARE.prepare(SimpleNamespace(input=folder/'source.png', geometry=folder/'geometry-long.json',
                                                out_dir=folder/'prepared-long', allow_legacy_geometry=False))
            data = {'reference': 'prepared-long/reference.png', 'reference_report': 'prepared-long/report.json',
                    'geometry': 'geometry-long.json', 'crop': 'auto'}
            (folder/'new.json').write_text(json.dumps(data))
            result = PIPELINE.run(folder/'new.json', folder/'result', new_generated=folder/'prepared-long/edit-input.png')
            with Image.open(result['output']) as image:
                self.assertEqual(image.getchannel('A').getextrema(), (255, 255))


class Adjustments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.pixels = np.random.default_rng(91).integers(0, 256, (36, 45, 4), dtype=np.uint8)
        self.pixels[0, :, 3] = 0
        self.pixels[1, :, 3] = 255
        self.master = self.folder/'master.png'
        Image.fromarray(self.pixels).save(self.master)
        self.original = self.master.read_bytes()

    def tearDown(self):
        self.assertEqual(self.master.read_bytes(), self.original)
        self.tmp.cleanup()

    def test_neutral_is_byte_exact_and_master_cannot_be_overwritten(self):
        out = self.folder/'neutral.png'
        COLOR.export(self.master, out)
        self.assertEqual(out.read_bytes(), self.original)
        for existing in (self.master, out):
            with self.assertRaises(FileExistsError):
                COLOR.export(self.master, existing, {'brightness': 10})

    def test_controls_keep_alpha_size_and_invisible_rgb(self):
        for key, value in [('brightness', 30), ('contrast', -20), ('saturation', -60), ('temperature', 25), ('hue', 70)]:
            output = COLOR.adjust(self.pixels, {key: value})
            self.assertEqual(output.shape, self.pixels.shape)
            self.assertTrue(np.array_equal(output[:, :, 3], self.pixels[:, :, 3]))
            self.assertTrue(np.array_equal(output[0], self.pixels[0]))
            self.assertFalse(np.array_equal(output[1:, :, :3], self.pixels[1:, :, :3]))

    def test_old_four_control_formula_is_preserved_for_visible_pixels(self):
        source = self.pixels.astype(float)
        r, g, b = source[:, :, 0]*1.044, source[:, :, 1], source[:, :, 2]*.956
        light = .2126*r+.7152*g+.0722*b
        expected = np.stack([np.rint(np.clip((light+(channel-light)*.7-128)*(2**.1)+128+9.2, 0, 255)) for channel in (r, g, b)], axis=-1).astype(np.uint8)
        actual = COLOR.adjust(self.pixels, {'brightness': 20, 'contrast': 10, 'saturation': -30, 'temperature': 20})
        np.testing.assert_array_equal(actual[:, :, :3][self.pixels[:, :, 3] > 0], expected[self.pixels[:, :, 3] > 0])

    def test_reject_invalid_settings_and_wrong_master(self):
        for values in ({'hue': 181}, {'temperature': float('nan')}, {'contrast': True}, {'filter': 1}):
            with self.assertRaises(ValueError):
                COLOR.export(self.master, self.folder/'bad.png', values)
            self.assertFalse((self.folder/'bad.png').exists())
        with self.assertRaisesRegex(ValueError, 'different master'):
            COLOR.export(self.master, self.folder/'wrong.png', {}, '0'*64)

    def test_offline_preview_contains_original_and_no_external_dependencies(self):
        result = COLOR.preview(self.master, self.folder/'preview')
        self.assertEqual(Path(result['initial']).read_bytes(), self.original)
        page = Path(result['preview']).read_text()
        self.assertNotIn('__ARTWORK_', page)
        self.assertNotIn('<script src=', page)
        self.assertNotIn('fetch(', page)
        self.assertEqual(result['master_sha256'], hashlib.sha256(self.original).hexdigest())

    @unittest.skipUnless(shutil.which('node'), 'Node only needed for browser/CLI parity validation')
    def test_preview_handlers_export_adjusted_state_while_comparing_and_reset(self):
        preview = COLOR.preview(self.master, self.folder/'page')
        result = subprocess.run(['node', str(ROOT/'tests/preview-interaction.cjs'), preview['preview'],
                                 str(self.folder/'export.png'), str(self.folder/'expected.bin')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with Image.open(self.folder/'export.png') as image:
            np.testing.assert_array_equal(np.array(image), COLOR.adjust(self.pixels, {'brightness': 35}))

    @unittest.skipUnless(shutil.which('node'), 'Node only needed for browser/CLI parity validation')
    def test_browser_and_cli_pixels_match_and_png_preserves_soft_alpha(self):
        values = {'brightness': 11, 'contrast': -17, 'saturation': 23, 'temperature': -19, 'hue': 34}
        (self.folder/'pixels.bin').write_bytes(self.pixels.tobytes())
        program = '''const fs = require('node:fs');
require('node:vm').runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const color = globalThis.ArtworkColor;
const input = new Uint8ClampedArray(fs.readFileSync(process.argv[2]));
const settings = JSON.parse(process.argv[3]);
const result = color.exportPixels(input, settings);
fs.writeFileSync(process.argv[4], result);
color.png(45, 36, result).then(async blob => fs.writeFileSync(process.argv[5], Buffer.from(await blob.arrayBuffer())));
'''
        subprocess.run(['node', '-e', program, str(SKILL/'assets/adjustment-core.js'), str(self.folder/'pixels.bin'),
                        json.dumps(values), str(self.folder/'browser.bin'), str(self.folder/'browser.png')], check=True, capture_output=True)
        browser = np.frombuffer((self.folder/'browser.bin').read_bytes(), dtype=np.uint8).reshape(self.pixels.shape)
        np.testing.assert_array_equal(browser, COLOR.adjust(self.pixels, values))
        with Image.open(self.folder/'browser.png') as image:
            np.testing.assert_array_equal(np.array(image), browser)
        # Reapplying neutral means returning to the immutable master, not the prior export.
        np.testing.assert_array_equal(COLOR.adjust(self.pixels, {}), self.pixels)


if __name__ == '__main__':
    unittest.main()
