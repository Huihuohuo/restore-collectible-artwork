"""Optional color adjustment after delivering a checked initial PNG. No model calls."""
import argparse
import base64
import hashlib
import html
import json
import math
from pathlib import Path
import zlib

import numpy as np
from PIL import Image

LIMITS = {'brightness': 100, 'contrast': 100, 'saturation': 100, 'temperature': 100, 'hue': 180}


def settings(values=None):
    values = {} if values is None else values
    if not isinstance(values, dict) or set(values) - LIMITS.keys():
        raise ValueError('Settings must be an object containing only brightness/contrast/saturation/temperature/hue')
    result = {}
    for key, limit in LIMITS.items():
        value = values.get(key, 0)
        if type(value) not in (float, int) or not math.isfinite(value) or abs(value) > limit:
            raise ValueError(f'{key} must be a finite number between {-limit} and {limit}')
        result[key] = value
    return result


def hue_matrix(degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [[.213+c*.787-s*.213, .715-c*.715-s*.715, .072-c*.072+s*.928],
            [.213-c*.213+s*.143, .715+c*.285+s*.140, .072-c*.072-s*.283],
            [.213-c*.213-s*.787, .715-c*.715+s*.715, .072+c*.928+s*.072]]


def adjust(source, values=None):
    p = settings(values)
    if source.dtype != np.uint8 or source.ndim != 3 or source.shape[2] != 4:
        raise ValueError('Expected uint8 RGBA image')
    result = source.copy()
    if not any(p.values()):
        return result
    brightness, contrast = p['brightness']/100*46, 2**(p['contrast']/100)
    saturation, warmth = 1+p['saturation']/100, p['temperature']/100*.22
    matrix = hue_matrix(p['hue'])
    # Work in row tiles to avoid large floating-point allocations on big masters.
    for y in range(0, source.shape[0], 128):
        raw = source[y:y+128]
        r, g, b = (raw[:, :, i].astype(float) for i in range(3))
        r *= 1+warmth
        b *= 1-warmth
        light = .2126*r+.7152*g+.0722*b
        channels = [light+(channel-light)*saturation for channel in (r, g, b)]
        if p['hue']:
            channels = [row[0]*channels[0]+row[1]*channels[1]+row[2]*channels[2] for row in matrix]
        for i, channel in enumerate(channels):
            values = np.rint(np.clip((channel-128)*contrast+128+brightness, 0, 255)).astype(np.uint8)
            result[y:y+128, :, i] = np.where(raw[:, :, 3] == 0, raw[:, :, i], values)
    return result


def load_master(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    with Image.open(path) as image:
        if image.format != 'PNG':
            raise ValueError('Use the restored initial PNG, not a new source photograph')
        if image.width > 4096 or image.height > 4096:
            raise ValueError('Master exceeds the current 4096px-per-edge limit')
        pixels = np.array(image.convert('RGBA'))
    return path, data, pixels, hashlib.sha256(data).hexdigest()


def export(master, output, values=None, expected_sha=None):
    path, data, pixels, digest = load_master(master)
    output = Path(output).resolve()
    record = output.with_suffix('.adjustments.json')
    p = settings(values)
    if expected_sha and expected_sha != digest:
        raise ValueError('Settings belong to a different master; choose the original matching PNG')
    if output.suffix.lower() != '.png':
        raise ValueError('Output must have a .png extension')
    if output == path or output.exists() or record.exists():
        raise FileExistsError('Use new output paths; never overwrite the master or an existing adjustment')
    output.parent.mkdir(parents=True, exist_ok=True)
    adjusted = adjust(pixels, p)
    with output.open('xb') as stream:
        if not any(p.values()):
            stream.write(data)
        else:
            Image.fromarray(adjusted).save(stream, format='PNG')
    report = {'stage': 'optional_color_adjustment', 'master_sha256': digest, 'settings': p,
              'size': [pixels.shape[1], pixels.shape[0]], 'alpha_preserved': bool(np.array_equal(pixels[:, :, 3], adjusted[:, :, 3])),
              'generation_calls': 0, 'output': output.name}
    with record.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def preview(master, out_dir):
    path, data, pixels, digest = load_master(master)
    out = Path(out_dir).resolve()
    if out.exists() or out.is_symlink():
        raise FileExistsError('Preview output folder must be new')
    assets = Path(__file__).resolve().parents[1]/'assets'
    payload = {'width': pixels.shape[1], 'height': pixels.shape[0], 'sha256': digest,
               'rgba': base64.b64encode(zlib.compress(pixels.tobytes(), 6)).decode('ascii'),
               'png': base64.b64encode(data).decode('ascii')}
    page = (assets/'adjustment.html').read_text(encoding='utf-8')
    page = page.replace('__ARTWORK_PAYLOAD__', json.dumps(payload)).replace('__ARTWORK_CORE__', (assets/'adjustment-core.js').read_text(encoding='utf-8'))
    out.mkdir(parents=True, exist_ok=False)
    (out/'index.html').write_text(page, encoding='utf-8')
    (out/'initial.png').write_bytes(data)
    return {'preview': str(out/'index.html'), 'initial': str(out/'initial.png'), 'master_sha256': digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('apply')
    p.add_argument('--master', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--settings', help='JSON parameters or settings exported from the preview')
    for key in LIMITS:
        p.add_argument('--'+key, type=float)
    p = sub.add_parser('preview')
    p.add_argument('--master', required=True)
    p.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'preview':
            result = preview(args.master, args.out_dir)
        else:
            document = json.loads(Path(args.settings).read_text(encoding='utf-8')) if args.settings else {}
            if not isinstance(document, dict):
                raise ValueError('Settings JSON must be an object')
            values = dict(document.get('settings', document))
            values.update({key: getattr(args, key) for key in LIMITS if getattr(args, key) is not None})
            result = export(args.master, args.output, values, document.get('master_sha256'))
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, TypeError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
