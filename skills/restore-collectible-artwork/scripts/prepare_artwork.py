"""Prepare an immutable geometry reference or normalize an edited bitmap.

Requires NumPy and Pillow. Performs no generative editing. All output folders
must be new; source images and existing approved artifacts are never replaced.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageOps


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def image(path):
    return ImageOps.exif_transpose(Image.open(path))


def new_folder(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def save_report(folder, data):
    (folder/'report.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return value


def output_size(spec, allow_legacy=False):
    """Lock a preset before rectification; old cases require explicit opt-in."""
    presets = json.loads((Path(__file__).resolve().parents[1]/'references/formats.json').read_text(encoding='utf-8'))
    format_id = spec.get('format_id')
    if allow_legacy and format_id is not None:
        raise ValueError('--allow-legacy-geometry is only for historical configs without format_id')
    if format_id is None:
        if not allow_legacy:
            raise ValueError('Set format_id to one of the five presets; historical configs require --allow-legacy-geometry')
        size = spec.get('output_size')
        metadata = {'format_id': None, 'legacy_geometry': True,
                    'note': 'Historical photo-estimated dimensions; not a current preset.'}
    elif format_id == 'custom':
        if not isinstance(spec.get('custom_reason'), str) or not spec['custom_reason'].strip():
            raise ValueError('custom requires custom_reason; do not use it to bypass the five card/picture presets')
        size = spec.get('output_size')
        metadata = {'format_id': 'custom', 'custom_reason': spec['custom_reason']}
    elif format_id not in presets:
        raise ValueError(f'Unknown format_id: {format_id}; choose from {", ".join(presets)}')
    else:
        preset = presets[format_id]
        basis_w, basis_h = preset['size_mm']
        if 'output_size' in spec and 'pixels_per_mm' in spec:
            raise ValueError('Choose either output_size or pixels_per_mm, not both')
        if 'output_size' in spec:
            size = spec['output_size']
        else:
            scale = positive_int(spec.get('pixels_per_mm', 20), 'pixels_per_mm')
            size = [basis_w*scale, basis_h*scale]
        if preset['target'] == 'picture_only':
            if spec.get('corner_radius_px', 0) != 0 or spec.get('mask_path'):
                raise ValueError('Instax presets prepare the opaque rectangular picture only; keep the frame separate')
        metadata = {'format_id': format_id, 'label': preset['label'],
                    'target': preset['target'], 'ratio_basis_mm': preset['size_mm'],
                    'frame_key': preset.get('frame_key')}
    if not isinstance(size, list) or len(size) != 2:
        raise ValueError('output_size must contain two positive integers')
    w, h = (positive_int(value, 'output_size') for value in size)
    if min(w, h) < 128 or max(w, h) > 4096:
        raise ValueError('output_size must be between 128 and 4096 per edge')
    if format_id in presets and w*presets[format_id]['size_mm'][1] != h*presets[format_id]['size_mm'][0]:
        raise ValueError('output_size does not match the selected format ratio; do not stretch after restoration')
    return (w, h), metadata


def prepare(args):
    source, config = Path(args.input), Path(args.geometry)
    spec = json.loads(config.read_text(encoding='utf-8'))
    (w, h), format_metadata = output_size(spec, args.allow_legacy_geometry)
    spec['output_size'] = [w, h]
    spec.pop('pixels_per_mm', None)
    im = image(source).convert('RGB')
    if list(im.size) != spec.get('source_size'):
        raise ValueError('geometry.source_size must match this EXIF-oriented input; do not reuse another photo calibration')
    quad = np.asarray(spec['quad'], dtype=float)
    if quad.shape != (4, 2) or not np.isfinite(quad).all():
        raise ValueError('quad must contain TL, TR, BR, BL source-pixel coordinates')
    if ((quad < 0).any() or (quad[:, 0] >= im.width).any()
            or (quad[:, 1] >= im.height).any()):
        raise ValueError('quad extends outside source image')
    edges = np.roll(quad, -1, axis=0)-quad
    cross = edges[:, 0]*np.roll(edges[:, 1], -1)-edges[:, 1]*np.roll(edges[:, 0], -1)
    if not ((cross > 1).all() or (cross < -1).all()):
        raise ValueError('quad must be convex and consistently ordered')
    radius = float(spec.get('corner_radius_px', 0))
    if not 0 <= radius <= min(w, h)/2:
        raise ValueError('invalid corner radius')
    matrix, values = [], []
    for (x, y), (u, v) in zip([(0, 0), (w-1, 0), (w-1, h-1), (0, h-1)], quad):
        matrix.extend([[x,y,1,0,0,0,-u*x,-u*y], [0,0,0,x,y,1,-v*x,-v*y]])
        values.extend([u, v])
    coefficients = np.linalg.solve(np.asarray(matrix, float), np.asarray(values, float))
    reference = im.transform((w, h), Image.Transform.PERSPECTIVE,
                             tuple(coefficients), Image.Resampling.BICUBIC).convert('RGBA')
    if spec.get('mask_path'):
        mask = Image.open(config.parent/spec['mask_path']).convert('L')
        if mask.size != (w, h):
            raise ValueError('mask_path size differs from output_size')
    else:
        supersample = 4 if max(w, h) <= 2048 else 2
        mask = Image.new('L', (w*supersample, h*supersample), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w*supersample-1, h*supersample-1),
                                              radius=radius*supersample, fill=255)
        mask = mask.resize((w, h), Image.Resampling.LANCZOS)
    reference.putalpha(mask)
    pad = int(spec.get('edit_gutter_px', 0 if format_metadata.get('target') == 'picture_only' else 32))
    if not 0 <= pad <= 128:
        raise ValueError('edit_gutter_px must be between 0 and 128')
    edit = Image.new('RGBA', (w+2*pad, h+2*pad), (0, 0, 0, 0))
    edit.alpha_composite(reference, (pad, pad))
    folder = new_folder(args.out_dir)
    shutil.copy2(source, folder/('source-original'+source.suffix.lower()))
    reference.save(folder/'reference.png')
    mask.save(folder/'mask.png')
    edit.save(folder/'edit-input.png')
    (folder/'geometry.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    save_report(folder, {'operation':'prepare', 'source_sha256':digest(source),
                         'geometry':spec, 'reference_size':[w,h],
                         'format':format_metadata,
                         'note':'Geometry reference only; photographed lighting/material is still present.'})
    print(folder.resolve())


def normalize(args):
    draft_path, reference_path = Path(args.input), Path(args.reference)
    draft, reference = image(draft_path).convert('RGBA'), image(reference_path).convert('RGBA')
    left, top, right, bottom = args.crop
    if not (0 <= left < right <= draft.width and 0 <= top < bottom <= draft.height):
        raise ValueError('crop must be the manually inspected card body, inside the edited bitmap')
    card = draft.crop((left, top, right, bottom))
    aspect_error = abs((card.width/card.height)/(reference.width/reference.height)-1)
    if aspect_error > .02:
        raise ValueError('Card-body aspect differs by >2%; inspect crop/geometry instead of stretching the image')
    alpha, rgb = np.asarray(card.getchannel('A')), np.array(card.convert('RGB'))
    for y in range(card.height):
        ids = np.flatnonzero(alpha[y] > 245)
        if not len(ids):
            raise ValueError('Crop includes an empty row; inspect the main card body rather than using noisy alpha bounds')
        rgb[y, :ids[0]] = rgb[y, ids[0]]
        rgb[y, ids[-1]+1:] = rgb[y, ids[-1]]
    # Uniform scaling plus centered trim, never independent X/Y stretching.
    scale = max(reference.width/card.width, reference.height/card.height)
    scaled = Image.fromarray(rgb).resize((round(card.width*scale), round(card.height*scale)),
                                         Image.Resampling.LANCZOS)
    x = (scaled.width-reference.width)//2
    y = (scaled.height-reference.height)//2
    output = scaled.crop((x, y, x+reference.width, y+reference.height)).convert('RGBA')
    output.putalpha(reference.getchannel('A'))
    folder = new_folder(args.out_dir)
    output.save(folder/'artwork.png')
    save_report(folder, {'operation':'normalize', 'input_sha256':digest(draft_path),
                         'reference_sha256':digest(reference_path), 'crop':args.crop,
                         'uniform_scale':scale, 'center_trim':[x,y],
                         'size':list(output.size), 'aspect_error_before_fit':aspect_error,
                         'note':'Mechanical edge cleanup and uniform fit only. Internal geometry still requires checking.'})
    print(folder.resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--input', required=True)
    p.add_argument('--geometry', required=True)
    p.add_argument('--out-dir', required=True)
    p.add_argument('--allow-legacy-geometry', action='store_true',
                   help='Reproduce an old no-format case without changing its approved proportions')
    p.set_defaults(run=prepare)
    p = sub.add_parser('normalize')
    p.add_argument('--input', required=True)
    p.add_argument('--reference', required=True)
    p.add_argument('--crop', required=True, nargs=4, type=int, metavar=('LEFT','TOP','RIGHT','BOTTOM'))
    p.add_argument('--out-dir', required=True)
    p.set_defaults(run=normalize)
    args = parser.parse_args()
    try:
        args.run(args)
    except (ValueError, FileExistsError, np.linalg.LinAlgError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    main()
