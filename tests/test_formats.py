import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prepare', ROOT/'skills/restore-collectible-artwork/scripts/prepare_artwork.py')
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)


class Formats(unittest.TestCase):
    def test_five_formats_export_exact_ratio_and_opaque_pictures(self):
        formats = json.loads((ROOT/'skills/restore-collectible-artwork/references/formats.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            Image.new('RGB', (300, 400), 'white').save(folder/'white.png')
            for name, info in formats.items():
                with self.subTest(format=name):
                    config = {'format_id': name, 'source_size': [300, 400],
                              'quad': [[0, 0], [299, 0], [299, 399], [0, 399]],
                              'pixels_per_mm': 4,
                              'corner_radius_px': 0 if info['target'] == 'picture_only' else 12}
                    geometry = folder/f'{name}.json'
                    geometry.write_text(json.dumps(config), encoding='utf-8')
                    PREPARE.prepare(SimpleNamespace(input=folder/'white.png', geometry=geometry,
                                                    out_dir=folder/name, allow_legacy_geometry=False))
                    with Image.open(folder/name/'reference.png') as output:
                        self.assertEqual(output.size, tuple(v*4 for v in info['size_mm']))
                        self.assertEqual(output.getpixel((output.width//2, output.height//2)), (255, 255, 255, 255))
                        if info['target'] == 'picture_only':
                            self.assertEqual(output.getchannel('A').getextrema(), (255, 255))
                        else:
                            self.assertEqual(output.getpixel((0, 0))[3], 0)

    def test_wrong_ratio_and_instax_border_mask_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'does not match'):
            PREPARE.output_size({'format_id': 'photocard-standard', 'output_size': [540, 860]})
        with self.assertRaisesRegex(ValueError, 'picture only'):
            PREPARE.output_size({'format_id': 'instax-mini', 'corner_radius_px': 5})
