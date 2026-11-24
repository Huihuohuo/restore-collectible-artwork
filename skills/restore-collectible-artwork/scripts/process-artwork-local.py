#!/usr/bin/env python3
"""Replay local artwork processing from an existing generated image; no model calls.

Paths in the JSON config are relative to that config, not the working directory.
Existing helper algorithms and safety thresholds are retained. Each run needs a
new output directory. See ../references/fast-path.md for the input contract.
"""
import time
PROCESS_STARTED = time.perf_counter()

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from types import SimpleNamespace

HELPERS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_helper(name):
    spec = importlib.util.spec_from_file_location(f'_local_{name}', HELPERS/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_config(path, *, require_generated=True):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    paths = {'config': path}
    for key in ['generated', 'reference', 'geometry', 'baseline', 'source', 'reference_report']:
        if key in data:
            paths[key] = (path.parent/data[key]).resolve()
            if not paths[key].is_file():
                if key == 'generated' and not require_generated:
                    paths.pop(key)
                    continue
                raise ValueError(f'Missing {key}: {paths[key]}')
    required = {'reference', 'geometry'} | ({'generated'} if require_generated else set())
    if not required <= paths.keys():
        raise ValueError('Config requires reference and geometry; also generated unless --new-generated is supplied')
    crop = data.get('crop')
    if data.get('generation_layout') not in (None, 'full_canvas', 'transparent_object'):
        raise ValueError('generation_layout must be full_canvas or transparent_object')
    if crop != 'auto' and (not isinstance(crop, list) or len(crop) != 4 or any(type(v) is not int for v in crop)):
        raise ValueError('crop must be four inspected integer bounds or the explicit value auto')
    return data, paths


def detect_body(path, expected_size, picture_only=False):
    """Find one centered, axis-aligned opaque card, ignoring sparse alpha debris.

    A majority across central bands is used rather than a noisy alpha bbox.
    Ambiguous/opaque-background/nonrectangular results remain manual cases.
    No RGB changes or target-aspect stretching occur here.
    """
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(path) as image:
        alpha = np.asarray(ImageOps.exif_transpose(image).convert('RGBA'))[:, :, 3]
    h, w = alpha.shape
    opaque = alpha > 245
    if not picture_only and np.mean(alpha < 16) < .00005:
        raise ValueError('Automatic card boundary needs a transparent exterior; inspect this image manually')

    def main_run(profile):
        ids = np.flatnonzero(profile >= .75)
        runs = np.split(ids, np.flatnonzero(np.diff(ids) != 1)+1)
        runs = [run for run in runs if len(run) >= len(profile)*.2]
        if len(runs) != 1:
            raise ValueError('Ambiguous generated-body boundaries; use an inspected crop')
        run = runs[0]
        if len(run) < len(profile)*.4 or not run[0] <= len(profile)/2 <= run[-1]:
            raise ValueError('Generated body is too small or off-center for automatic cropping')
        return int(run[0]), int(run[-1])+1

    left, right = main_run(opaque[int(h*.4):int(h*.6)].mean(axis=0))
    top, bottom = main_run(opaque[:, int(w*.4):int(w*.6)].mean(axis=1))
    crop = [left, top, right, bottom]
    aspect_error = abs(((right-left)/(bottom-top))/(expected_size[0]/expected_size[1])-1)
    coverage = float(opaque[top:bottom, left:right].mean())
    margin = max(2, round(max(w, h)*.015))
    outside = opaque.copy()
    outside[max(0, top-margin):min(h, bottom+margin), max(0, left-margin):min(w, right+margin)] = False
    extra_fraction = float(outside.sum()/((right-left)*(bottom-top)))
    if aspect_error > .02 or coverage < .98 or extra_fraction > .01:
        raise ValueError('Generated-body shape/aspect/extra-object checks failed; inspect rather than forcing a crop')
    return {'crop': crop, 'method': 'central-band opaque-majority runs',
            'aspect_error_before_fit': aspect_error, 'opaque_coverage': coverage,
            'distant_extra_opaque_fraction': extra_fraction,
            'note': 'Geometry detection only; does not establish content or color fidelity.'}


def verify_pixels(candidate, reference, expected_size, baseline=None):
    import numpy as np
    from PIL import Image
    with Image.open(candidate) as image:
        if image.format != 'PNG' or image.mode != 'RGBA':
            raise ValueError('Output must be PNG/RGBA')
        actual = np.array(image)
        size = image.size
    with Image.open(reference) as image:
        alpha = np.array(image.convert('RGBA').getchannel('A'))
    checks = {'size': list(size), 'fixed_format_size': list(size) == list(expected_size),
              'alpha_matches_reference': actual.shape[:2] == alpha.shape and bool(np.array_equal(actual[:, :, 3], alpha)),
              'fully_opaque_reference_pixels_preserved': actual.shape[:2] == alpha.shape and bool(np.all(actual[:, :, 3][alpha == 255] == 255))}
    if baseline:
        with Image.open(baseline) as image:
            gold = np.array(image.convert('RGBA'))
        same_size = actual.shape == gold.shape
        delta = np.abs(actual.astype(np.int16)-gold.astype(np.int16)) if same_size else None
        checks['approved_baseline'] = {
            'rgba_pixel_exact': same_size and bool(np.array_equal(actual, gold)),
            'changed_pixels': int(np.any(delta != 0, axis=2).sum()) if same_size else None,
            'max_rgb_error': int(delta[:, :, :3].max()) if same_size else None,
            'max_alpha_error': int(delta[:, :, 3].max()) if same_size else None,
            'rgba_sha256': hashlib.sha256(actual.tobytes()).hexdigest(),
            'baseline_rgba_sha256': hashlib.sha256(gold.tobytes()).hexdigest(),
        }
    checks['passed'] = (checks['fixed_format_size'] and checks['alpha_matches_reference']
                        and checks['fully_opaque_reference_pixels_preserved']
                        and (not baseline or checks['approved_baseline']['rgba_pixel_exact']))
    return checks


def run(config, out_dir, *, rebuild_reference=False, diagnostics=False,
        execution='in-process', started_at=None, auto_crop=False, new_generated=None):
    started_at = time.perf_counter() if started_at is None else started_at
    out_dir = Path(out_dir).resolve()
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f'Output already exists; nothing overwritten: {out_dir}')
    steps = {}
    setup = time.perf_counter()
    prepare = load_helper('prepare_artwork')
    align = load_helper('align_artwork')
    import PIL
    import numpy as np
    data, paths = read_config(config, require_generated=new_generated is None)
    new_generation = new_generated is not None
    if new_generation:
        replacement = Path(new_generated).resolve()
        if not replacement.is_file():
            raise ValueError(f'Missing new generated image: {replacement}')
        paths['generated'] = replacement
        # A new generation is not supposed to match the old generation's pixels.
        # Other immutable input fingerprints and all geometry checks remain.
        paths.pop('baseline', None)
        auto_crop = True
    geometry = json.loads(paths['geometry'].read_text(encoding='utf-8'))
    expected_size, format_metadata = prepare.output_size(geometry)
    if rebuild_reference and 'source' not in paths:
        raise ValueError('--rebuild-reference needs source and the existing calibrated geometry')
    if not rebuild_reference:
        metadata_path = paths.get('reference_report', paths['reference'].parent/'report.json')
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        resolved = {**geometry, 'output_size': list(expected_size)}
        resolved.pop('pixels_per_mm', None)
        resolved.pop('long_edge_px', None)
        if metadata.get('geometry') != resolved:
            raise ValueError('Cached reference geometry differs; use --rebuild-reference with the correct source')
        paths['reference_report'] = metadata_path
    before = {key: digest(path) for key, path in paths.items()}
    for key, expected in data.get('expected_sha256', {}).items():
        if new_generation and key in {'generated', 'baseline'}:
            continue
        if key not in before or before[key] != expected:
            raise ValueError(f'Input fingerprint differs: {key}; inspect the config rather than reusing a stale crop')
    steps['setup'] = time.perf_counter()-setup
    out_dir.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'format': format_metadata, 'execution': execution,
              'diagnostics': diagnostics, 'input_sha256': before,
              'inputs': {key: str(path) for key, path in paths.items()},
              'crop': 'auto' if auto_crop else data['crop'], 'steps_seconds': steps,
              'scope': {'model_calls': 0, 'generated_image_reused': not new_generation,
                        'source_quad_reused': True, 'generated_body_crop_reused': not (auto_crop or data['crop'] == 'auto'),
                        'new_generation_input': new_generation,
                        'reference_mode': 'rebuild_from_known_quad' if rebuild_reference else 'reuse_prepared_reference',
                        'new_upload_detection_included': False, 'generation_included': False},
              'runtime': {'python': platform.python_version(), 'numpy': np.__version__,
                          'pillow': PIL.__version__, 'executable': sys.executable},
              'helper_sha256': {name: digest(HELPERS/name) for name in ['prepare_artwork.py', 'align_artwork.py']}}

    def invoke(name, function, args, command):
        start = time.perf_counter()
        try:
            if execution == 'subprocess':
                result = subprocess.run([sys.executable, *map(str, command)], capture_output=True, text=True)
                if result.returncode:
                    (out_dir/f'{name}-error.log').write_text(result.stdout+result.stderr, encoding='utf-8')
                    raise ValueError(f'{name} failed: {result.stderr.strip()}')
            else:
                with redirect_stdout(io.StringIO()):
                    function(SimpleNamespace(**args))
        finally:
            steps[name] = time.perf_counter()-start

    try:
        reference = paths['reference']
        if rebuild_reference:
            args = {'input': paths['source'], 'geometry': paths['geometry'],
                    'out_dir': out_dir/'prepared', 'allow_legacy_geometry': False}
            invoke('prepare', prepare.prepare, args,
                   [HELPERS/'prepare_artwork.py', 'prepare', '--input', paths['source'],
                    '--geometry', paths['geometry'], '--out-dir', out_dir/'prepared'])
            reference = out_dir/'prepared/reference.png'
        else:
            steps['prepare'] = 0.0
        contract = prepare.generation_contract(reference, data.get('generation_layout'))
        report['generation'] = contract
        crop = data['crop']
        if contract['layout'] == 'full_canvas':
            start = time.perf_counter()
            report['body_detection'] = prepare.full_canvas_crop(paths['generated'], expected_size)
            if not auto_crop and crop != 'auto' and crop != report['body_detection']['crop']:
                raise ValueError('Full-canvas artwork must retain the entire generated picture; inspect the output contract')
            crop = report['body_detection']['crop']
            steps['detect_body'] = time.perf_counter()-start
        elif auto_crop or crop == 'auto':
            start = time.perf_counter()
            report['body_detection'] = detect_body(paths['generated'], expected_size)
            crop = report['body_detection']['crop']
            steps['detect_body'] = time.perf_counter()-start
        report['crop'] = crop
        args = {'input': paths['generated'], 'reference': reference, 'crop': crop, 'out_dir': out_dir/'normalized'}
        invoke('normalize', prepare.normalize, args,
               [HELPERS/'prepare_artwork.py', 'normalize', '--input', paths['generated'], '--reference', reference,
                '--crop', *crop, '--out-dir', out_dir/'normalized'])
        args = {'reference': reference, 'artwork': out_dir/'normalized/artwork.png',
                'out_dir': out_dir/'alignment', 'mode': 'auto', 'max_edge_fraction': .015,
                'diagnostics': diagnostics}
        command = [HELPERS/'align_artwork.py', '--reference', reference,
                   '--artwork', out_dir/'normalized/artwork.png', '--out-dir', out_dir/'alignment', '--mode', 'auto']
        if not diagnostics:
            command.append('--no-diagnostics')
        invoke('align', align.run, args, command)
        start = time.perf_counter()
        alignment_report = json.loads((out_dir/'alignment/report.json').read_text(encoding='utf-8'))
        report['alignment'] = {key: alignment_report[key] for key in
                               ['status', 'mode', 'before', 'after_held_out', 'edge_extension_analysis_px']}
        quality = verify_pixels(out_dir/'alignment/aligned.png', reference, expected_size, paths.get('baseline'))
        report['quality'] = quality
        report['inputs_unchanged'] = before == {key: digest(path) for key, path in paths.items()}
        steps['verify'] = time.perf_counter()-start
        if not quality['passed'] or not report['inputs_unchanged']:
            raise ValueError('Quality check failed; final PNG not exported. Inspect run.json and alignment outputs.')
        start = time.perf_counter()
        shutil.copyfile(out_dir/'alignment/aligned.png', out_dir/'digital-artwork.png')
        steps['export'] = time.perf_counter()-start
        report['output'] = str(out_dir/'digital-artwork.png')
        report['delivery'] = {'stage': 'restored_initial', 'master': report['output'],
                              'visual_review': 'required_before_presenting_as_checked',
                              'next': 'Show and offer the initial PNG first; color adjustment is optional.',
                              'adjustments_applied': False}
        report['status'] = 'matches_approved_baseline' if 'baseline' in paths else 'local_checks_passed_requires_visual_review'
        report['total_seconds'] = time.perf_counter()-started_at
        (out_dir/'run.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        return report
    except Exception as error:
        report.update(status='failed', error=str(error), total_seconds=time.perf_counter()-started_at)
        (out_dir/'run.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Replay JSON; relative input paths resolve beside this file')
    parser.add_argument('--out-dir', required=True, help='New output directory; existing paths are refused')
    parser.add_argument('--rebuild-reference', action='store_true', help='Include rectification with the existing known quad; not new-photo detection')
    parser.add_argument('--diagnostics', action='store_true', help='Also save overlay PNGs and the alignment field')
    parser.add_argument('--auto-crop', action='store_true', help='Use the complete opaque canvas or locate one transparent object, according to the reference silhouette')
    parser.add_argument('--new-generated', help='Process a NEW generation using the output contract; preserve source guards, do not compare old generated pixels')
    parser.add_argument('--execution', choices=['in-process', 'subprocess'], default='in-process', help='subprocess is provided for measuring the original separate CLI calls')
    args = parser.parse_args()
    try:
        report = run(args.config, args.out_dir, rebuild_reference=args.rebuild_reference,
                     diagnostics=args.diagnostics, execution=args.execution, started_at=PROCESS_STARTED,
                     auto_crop=args.auto_crop, new_generated=args.new_generated)
        print(json.dumps({key: report[key] for key in ['status', 'output', 'steps_seconds', 'total_seconds']}, ensure_ascii=False))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
