"""Prepare an immutable geometry reference or normalize an edited bitmap.

Requires NumPy and Pillow. Performs no generative editing. All output folders
must be new; source images and existing approved artifacts are never replaced.
"""
import argparse
import hashlib
import json
import math
from fractions import Fraction
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


def generation_contract(reference, layout=None):
    """RGB layout is independent of the final silhouette; never key artwork colors."""
    reference = reference if isinstance(reference, Image.Image) else image(reference)
    rectangular = reference.convert('RGBA').getchannel('A').getextrema() == (255, 255)
    if layout is None:
        layout = 'full_canvas' if rectangular else 'transparent_object'
    if layout not in {'full_canvas', 'transparent_object'}:
        raise ValueError('generation_layout must be full_canvas or transparent_object')
    common = ('Preserve ALL content and backgrounds inside the artwork, including white, black, '
              'gradients and dark scenes. Do not extract the person or key out any color. '
              'Keep the original composition, crop and aspect ratio; no new border, shadow or material. ')
    output = ('Return the complete opaque rectangular artwork edge-to-edge, filling the entire canvas. '
              'No transparent pixels, gutter or external surroundings.' if layout == 'full_canvas' else
              'Keep the complete object interior opaque. Only the area outside the object silhouette '
              'may be genuinely transparent; do not erase backgrounds printed inside it.')
    return {'layout': layout, 'transparent_background': layout == 'transparent_object',
            'final_alpha': 'immutable_reference',
            'output_requirements': common + output}


def opaque_edit_input(reference):
    """Extend only exterior row ends for an opaque host, without changing the master.

    No photographed surroundings or matte color is inserted. These temporary
    corner extensions are discarded when the reference alpha is restored.
    Disconnected silhouettes/holes need a different explicitly reviewed input.
    """
    rgba = np.array(reference.convert('RGBA'))
    rgb = rgba[:, :, :3].copy()
    for y, alpha in enumerate(rgba[:, :, 3]):
        ids = np.flatnonzero(alpha == 255)
        # Supersampled masks can ring by a few alpha levels near a curve.
        if not len(ids) or np.any(alpha[ids[0]:ids[-1]+1] < 245):
            raise ValueError('Opaque edit input requires a solid row-connected silhouette; inspect the mask')
        rgb[y, :ids[0]] = rgb[y, ids[0]]
        rgb[y, ids[-1]+1:] = rgb[y, ids[-1]]
    return Image.fromarray(rgb)


def full_canvas_crop(draft, expected_size):
    """Reject missing picture pixels before normalization can replace their alpha."""
    draft = draft if isinstance(draft, Image.Image) else image(draft)
    if draft.convert('RGBA').getchannel('A').getextrema() != (255, 255):
        raise ValueError('Full-canvas artwork must be completely opaque; in-picture background may have been removed')
    error = abs((draft.width/draft.height)/(expected_size[0]/expected_size[1])-1)
    if error > .02:
        raise ValueError('Full-canvas aspect differs by >2%; do not crop or stretch it to hide the mismatch')
    return {'crop': [0, 0, draft.width, draft.height], 'method': 'complete opaque canvas',
            'aspect_error_before_fit': error, 'opaque_coverage': 1.0,
            'note': 'No color-based background removal. Content/composition still require visual review.'}


def rounded_mask(size, radius):
    w, h = size
    supersample = 4 if max(w, h) <= 2048 else 2
    mask = Image.new('L', (w*supersample, h*supersample), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w*supersample-1, h*supersample-1),
                                          radius=radius*supersample, fill=255)
    return mask.resize((w, h), Image.Resampling.LANCZOS)


def positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return value


def output_size(spec, allow_legacy=False):
    """Validate confirmed intake, then lock a preset or a custom target ratio."""
    presets = json.loads((Path(__file__).resolve().parents[1]/'references/formats.json').read_text(encoding='utf-8'))
    format_id = spec.get('format_id')
    if allow_legacy:
        if format_id is not None:
            raise ValueError('--allow-legacy-geometry is only for historical configs without format_id')
        size = spec.get('output_size')
        metadata = {'format_id': None, 'legacy_geometry': True, 'target': 'whole_object'}
    else:
        if format_id is None:
            raise ValueError('Set an explicit format_id: a common preset or custom; ask for object and dimensions first')
        if format_id not in presets and format_id != 'custom':
            raise ValueError('Unknown format_id; choose a common preset or custom')
        if not isinstance(spec.get('object_type'), str) or not spec['object_type'].strip():
            raise ValueError('Missing object_type; ask what the object is before processing')
        dimensions = spec.get('dimensions')
        if not isinstance(dimensions, dict) or dimensions.get('unit') not in {'mm', 'cm', 'in', 'px'}:
            raise ValueError('Missing/invalid dimensions; ask for width, height and unit (mm/cm/in/px)')
        values = []
        for key in ('width', 'height'):
            value = dimensions.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('dimensions width and height must be positive finite numbers')
            if dimensions['unit'] == 'px' and not float(value).is_integer():
                raise ValueError('Pixel dimensions must be integers')
            values.append(value)
        width, height = values
        ratio = width / height
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError('Invalid target ratio')
        preset = presets.get(format_id)
        target = ('picture_only' if preset['target'] == 'picture_only' else 'whole_object') if preset else spec.get('target')
        if target not in {'whole_object', 'picture_only'}:
            raise ValueError('custom requires target: whole_object or picture_only; clarify which area the dimensions describe')
        if 'target' in spec and spec['target'] != target:
            raise ValueError('target conflicts with selected preset')
        unit_scale = {'mm': 1, 'cm': 10, 'in': 25.4}.get(dimensions['unit'])
        if preset:
            basis_w, basis_h = preset['size_mm']
            if unit_scale is None:
                matches = math.isclose(ratio, basis_w / basis_h, rel_tol=1e-10)
            else:
                matches = (math.isclose(width * unit_scale, basis_w, rel_tol=1e-10)
                           and math.isclose(height * unit_scale, basis_h, rel_tol=1e-10))
            if not matches:
                raise ValueError('Confirmed dimensions differ from the preset; use custom instead of changing the user dimensions')
        choices = sum(key in spec for key in ('output_size', 'pixels_per_mm', 'long_edge_px'))
        if choices > 1:
            raise ValueError('Choose only one of output_size, pixels_per_mm or long_edge_px')
        if 'output_size' in spec:
            size = spec['output_size']
        elif 'pixels_per_mm' in spec:
            if unit_scale is None:
                raise ValueError('pixels_per_mm needs physical units; use output_size for pixels')
            scale = positive_int(spec['pixels_per_mm'], 'pixels_per_mm')
            size = [round(width * unit_scale * scale), round(height * unit_scale * scale)]
        elif preset and 'long_edge_px' not in spec:
            size = [v * 20 for v in preset['size_mm']]
        elif dimensions['unit'] == 'px' and 'long_edge_px' not in spec:
            size = [int(width), int(height)]
        else:
            edge = positive_int(spec.get('long_edge_px', 1700), 'long_edge_px')
            # Prefer an exact integer ratio whenever a useful multiple fits.
            fraction = Fraction(str(width)) / Fraction(str(height))
            rw, rh = fraction.numerator, fraction.denominator
            multiple = edge // max(rw, rh)
            if multiple and min(rw, rh) * multiple >= 128:
                size = [rw * multiple, rh * multiple]
            else:
                size = [edge, round(edge / ratio)] if ratio >= 1 else [round(edge * ratio), edge]
        if target == 'picture_only' and (spec.get('corner_radius_px', 0) != 0 or spec.get('mask_path')):
            raise ValueError('picture only targets are opaque rectangles; keep the frame separate')
        metadata = {'format_id': format_id, 'object_type': spec['object_type'].strip(),
                    'dimensions': dimensions, 'target': target,
                    'label': preset['label'] if preset else spec['object_type'].strip(),
                    'frame_key': preset.get('frame_key') if preset else None}
        if preset:
            metadata['ratio_basis_mm'] = preset['size_mm']
    if not isinstance(size, list) or len(size) != 2:
        raise ValueError('output_size must contain two positive integers')
    w, h = (positive_int(value, 'output_size') for value in size)
    if min(w, h) < 128 or max(w, h) > 4096:
        raise ValueError('output_size must be between 128 and 4096 per edge; choose a suitable pixel resolution')
    if not allow_legacy:
        if preset and w * preset['size_mm'][1] != h * preset['size_mm'][0]:
            raise ValueError('output_size does not match the selected format ratio; do not stretch after restoration')
        # Only unavoidable short-edge pixel rounding is allowed for custom sizes.
        error = abs(h - w / ratio) if ratio >= 1 else abs(w - h * ratio)
        if error > .50000001:
            raise ValueError('output_size does not match confirmed dimensions; do not stretch after restoration')
        metadata['short_edge_rounding_px'] = error
    return (w, h), metadata


def prepare(args):
    source, config = Path(args.input), Path(args.geometry)
    spec = json.loads(config.read_text(encoding='utf-8'))
    if 'geometry_review' in spec and spec['geometry_review'] != 'accepted':
        raise ValueError('Geometry diagnostics are pending review; inspect source/corner/edge comparisons, resolve uncertainty, then set geometry_review: "accepted"')
    if 'geometry_review' in spec and spec.get('corner_radius_px') is None and not spec.get('mask_path'):
        raise ValueError('Resolve the uncertain corner radius or supply a reviewed mask; do not silently use zero')
    (w, h), format_metadata = output_size(spec, args.allow_legacy_geometry)
    spec['output_size'] = [w, h]
    spec.pop('pixels_per_mm', None)
    spec.pop('long_edge_px', None)
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
        mask = rounded_mask((w, h), radius)
    unmasked = reference.convert('RGB')
    reference.putalpha(mask)
    contract = generation_contract(reference, spec.get('generation_layout'))
    pad = int(spec.get('edit_gutter_px', 0 if contract['layout'] == 'full_canvas' else 32))
    if not 0 <= pad <= 128:
        raise ValueError('edit_gutter_px must be between 0 and 128')
    if contract['layout'] == 'full_canvas' and spec.get('generation_layout') == 'full_canvas':
        if pad:
            raise ValueError('Explicit full_canvas requires edit_gutter_px: 0 (or omit it)')
        edit = opaque_edit_input(reference)
    else:
        edit = Image.new('RGBA', (w+2*pad, h+2*pad), (0, 0, 0, 0))
        edit.alpha_composite(reference, (pad, pad))
    folder = new_folder(args.out_dir)
    shutil.copy2(source, folder/('source-original'+source.suffix.lower()))
    reference.save(folder/'reference.png')
    unmasked.save(folder/'rectified-unmasked.png')
    mask.save(folder/'mask.png')
    edit.save(folder/'edit-input.png')
    (folder/'geometry.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    save_report(folder, {'operation':'prepare', 'source_sha256':digest(source),
                         'geometry':spec, 'reference_size':[w,h],
                         'format':format_metadata,
                         'generation':{**contract, 'recommended_input': 'reference.png' if contract['layout'] == 'full_canvas' and pad else 'edit-input.png'},
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
    # Allow the existing small outline/rounding variation, but never hide an
    # erased in-picture background by restoring alpha or smearing row edges.
    margin = max(2, round(min(card.size)*.02))
    expected_alpha = reference.getchannel('A').resize(card.size, Image.Resampling.NEAREST)
    blocked = np.pad(np.asarray(expected_alpha) < 255, margin, constant_values=True)
    summed = np.pad(blocked, ((1, 0), (1, 0))).cumsum(0, dtype=np.int32).cumsum(1, dtype=np.int32)
    span = 2*margin+1
    interior = (summed[span:, span:]-summed[:-span, span:]
                -summed[span:, :-span]+summed[:-span, :-span]) == 0
    if np.any(alpha[interior] <= 245):
        raise ValueError('Generated artwork has transparent interior pixels; preserve in-picture backgrounds before normalization')
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
                         'opaque_interior_checked': True,
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
