"""Build a deterministic geometric test pattern; no personal photos or model calls."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def create_fixture(folder, pipeline, geometry_overrides=None):
    folder = Path(folder)
    rng = np.random.default_rng(20261122)
    pixels = rng.integers(70, 210, (850, 540, 3), dtype=np.uint8)
    picture = Image.fromarray(pixels).filter(ImageFilter.GaussianBlur(2))
    draw = ImageDraw.Draw(picture)
    for i in range(500):
        x, y = int(rng.integers(0, 500)), int(rng.integers(0, 810))
        width, height = int(rng.integers(6, 40)), int(rng.integers(6, 40))
        color = tuple(int(v) for v in rng.integers(5, 250, 3))
        if i % 2:
            draw.rectangle((x, y, x+width, y+height), fill=color)
        else:
            draw.ellipse((x, y, x+width, y+height), fill=color)
    draw.rectangle((240, 390, 300, 440), fill='white')
    picture.save(folder/'source.png')
    geometry = {
        'format_id': 'photocard-standard',
        'object_type': 'photocard', 'dimensions': {'width': 54, 'height': 85, 'unit': 'mm'},
        'source_size': [540, 850],
        'quad': [[0, 0], [539, 0], [539, 849], [0, 849]],
        'output_size': [540, 850], 'corner_radius_px': 0, 'edit_gutter_px': 30,
    }
    geometry.update(geometry_overrides or {})
    if geometry.get('target') == 'picture_only' and 'edit_gutter_px' not in (geometry_overrides or {}):
        geometry['edit_gutter_px'] = 0
    (folder/'geometry.json').write_text(json.dumps(geometry), encoding='utf-8')
    prepare = pipeline.load_helper('prepare_artwork')
    with contextlib.redirect_stdout(io.StringIO()):
        prepare.prepare(SimpleNamespace(input=folder/'source.png', geometry=folder/'geometry.json',
                                        out_dir=folder/'prepared', allow_legacy_geometry=False))
    (folder/'generated.png').write_bytes((folder/'prepared/edit-input.png').read_bytes())
    (folder/'baseline.png').write_bytes((folder/'prepared/reference.png').read_bytes())
    (folder/'prompt.txt').write_text('Synthetic geometry test. No model is called.', encoding='utf-8')
    with Image.open(folder/'prepared/reference.png') as reference:
        width, height = reference.size
    with Image.open(folder/'prepared/edit-input.png') as edit_input:
        pad = (edit_input.width-width)//2
    config = {
        'source': 'source.png', 'geometry': 'geometry.json',
        'reference': 'prepared/reference.png', 'reference_report': 'prepared/report.json',
        'generated': 'generated.png', 'baseline': 'baseline.png', 'crop': [pad, pad, width+pad, height+pad],
        # Keep the older padded-rectangle replay as an explicit compatibility case.
        'generation_layout': 'transparent_object' if pad else 'full_canvas',
    }
    config['expected_sha256'] = {
        key: pipeline.digest(folder/config[key]) for key in
        ('source', 'geometry', 'reference', 'reference_report', 'generated', 'baseline')
    }
    path = folder/'replay.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path
