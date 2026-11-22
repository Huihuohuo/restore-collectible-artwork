#!/usr/bin/env python3
"""Start/finish a controlled built-in-image-tool job; never calls an image API.

The host calls the image tool between start and finish. Finish accepts exactly
one newly saved PNG in the snapshotted tool directory, then runs all local work.
This filesystem handoff is for an isolated local trial, not a concurrent server.
"""
import time
PROCESS_STARTED = time.perf_counter()
PROCESS_UNIX = time.time()

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location('artwork_local', Path(__file__).with_name('process-artwork-local.py'))
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def inventory(root):
    return sorted(str(path.resolve()) for path in root.rglob('*.png') if path.is_file())


def start(args):
    folder = Path(args.job_dir).resolve()
    if folder.exists() or folder.is_symlink():
        raise FileExistsError(f'Job directory exists; nothing overwritten: {folder}')
    config = Path(args.config).resolve()
    data, paths = PIPELINE.read_config(config, require_generated=False)
    prompt, edit_input = Path(args.prompt).resolve(), Path(args.edit_input).resolve()
    root = Path(args.tool_dir).resolve()
    if not root.is_dir() or not prompt.is_file() or not edit_input.is_file():
        raise ValueError('Tool directory, prompt and edit input must already exist')
    protected = {str(path): PIPELINE.digest(path) for path in [*paths.values(), prompt, edit_input]}
    for key, expected in data.get('expected_sha256', {}).items():
        if key not in paths or protected[str(paths[key])] != expected:
            raise ValueError(f'Input fingerprint differs before generation: {key}')
    geometry = json.loads(paths['geometry'].read_text(encoding='utf-8'))
    prepare = PIPELINE.load_helper('prepare_artwork')
    size, format_metadata = prepare.output_size(geometry)
    resolved = {**geometry, 'output_size': list(size)}
    resolved.pop('pixels_per_mm', None)
    metadata = paths.get('reference_report', paths['reference'].parent/'report.json')
    if json.loads(metadata.read_text(encoding='utf-8')).get('geometry') != resolved:
        raise ValueError('Cached reference geometry differs before generation')
    protected[str(metadata)] = PIPELINE.digest(metadata)
    # Stable prompt and edit input are reused byte-for-byte; no new prompting.
    state = {'status': 'waiting_for_one_generation', 'started_perf': PROCESS_STARTED,
             'started_unix': PROCESS_UNIX, 'config': str(config), 'prompt': str(prompt),
             'edit_input': str(edit_input), 'tool_dir': str(root),
             'protected_sha256': protected, 'existing_pngs': inventory(root),
             'scope': 'Reused source quad/reference/prompt; no first-upload detection or website API.'}
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder/'job.json', state)
    return {'job_dir': str(folder), 'prompt': prompt.read_text(encoding='utf-8'), 'edit_input': str(edit_input),
            'transparent_background': format_metadata.get('target') == 'whole_card',
            'preflight_seconds': time.perf_counter()-PROCESS_STARTED}


def comparison(paths, destination):
    from PIL import Image, ImageDraw, ImageOps
    width, height = 400, 630
    sheet = Image.new('RGB', (width*len(paths), height+40), '#eeeeee')
    draw = ImageDraw.Draw(sheet)
    for index, (label, path) in enumerate(paths):
        tile = Image.new('RGBA', (width, height), '#e8e8e8')
        checker = ImageDraw.Draw(tile)
        for y in range(0, height, 16):
            for x in range(0, width, 16):
                if (x//16+y//16) % 2:
                    checker.rectangle((x, y, x+15, y+15), fill='#ffffff')
        with Image.open(path) as image:
            image = ImageOps.contain(image.convert('RGBA'), (width, height), Image.Resampling.LANCZOS)
        tile.alpha_composite(image, ((width-image.width)//2, (height-image.height)//2))
        sheet.paste(tile.convert('RGB'), (index*width, 40))
        draw.text((index*width+12, 14), label, fill='#222222')
    sheet.save(destination, quality=93)


def finish(args):
    folder = Path(args.job_dir).resolve()
    state_path = folder/'job.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    if state['status'] != 'waiting_for_one_generation':
        raise ValueError('Job already finished or failed; start a new job')
    before_tool = args.tool_started_unix-state['started_unix']
    generation = args.tool_ended_unix-args.tool_started_unix
    if before_tool < 0 or generation <= 0 or args.tool_ended_unix > time.time()+1:
        raise ValueError('Invalid tool timestamps; use the same machine wall clock')
    state['status'] = 'processing'
    write_json(state_path, state)
    try:
        # Verify every preflight input, not only those named in expected_sha256.
        for path, expected in state['protected_sha256'].items():
            if PIPELINE.digest(path) != expected:
                raise ValueError(f'Protected input changed during generation: {path}')
        candidates = sorted(set(inventory(Path(state['tool_dir'])))-set(state['existing_pngs']))
        if len(candidates) != 1:
            raise ValueError(f'Expected exactly one new tool PNG; found {len(candidates)}. No newest-file guessing.')
        source = Path(candidates[0])
        shutil.copyfile(source, folder/'generated.png')
        handoff_seconds = time.perf_counter()-PROCESS_STARTED
        local = PIPELINE.run(state['config'], folder/'result', new_generated=folder/'generated.png')
        png_ready = time.perf_counter()-state['started_perf']
        _, inputs = PIPELINE.read_config(state['config'], require_generated=False)
        images = [('RECTIFIED SOURCE', inputs['reference'])]
        if 'baseline' in inputs:
            images.append(('APPROVED REFERENCE', inputs['baseline']))
        images.append(('NEW ALIGNED CANDIDATE', Path(local['output'])))
        compare_start = time.perf_counter()
        comparison(images, folder/'comparison.jpg')
        compare_seconds = time.perf_counter()-compare_start
        for path, expected in state['protected_sha256'].items():
            if PIPELINE.digest(path) != expected:
                raise ValueError(f'Protected input changed during processing: {path}')
        now = time.perf_counter()
        total = now-state['started_perf']
        report = {'status': local['status'], 'model_calls': 1,
                  'tool_source': str(source), 'scope': state['scope'],
                  'steps_seconds': {'preflight_to_tool_request': before_tool,
                                    'image_tool_request_return': generation,
                                    'finish_preflight_and_handoff': handoff_seconds,
                                    'local_pipeline': local['total_seconds'],
                                    'comparison': compare_seconds},
                  'png_ready_seconds': png_ready, 'candidate_and_comparison_ready_seconds': total,
                  'other_handoff_and_reporting_seconds': total-before_tool-generation-handoff_seconds-local['total_seconds']-compare_seconds,
                  'local_steps_seconds': local['steps_seconds'], 'body_detection': local['body_detection'],
                  'quality': local['quality'], 'alignment': local['alignment'],
                  'protected_inputs_unchanged': True,
                  'output': local['output'], 'comparison': str(folder/'comparison.jpg'),
                  'prompt': state['prompt'], 'prompt_sha256': state['protected_sha256'][state['prompt']],
                  'visual_review': 'pending; geometry/file checks are not content or color approval',
                  'excluded': ['development and earlier conversation time', 'first-upload source-corner detection',
                               'human visual review and final delivery', 'website upload/API/queue/concurrency']}
        write_json(folder/'timing-report.json', report)
        state.update(status='candidate_ready_requires_visual_review', report=str(folder/'timing-report.json'))
        write_json(state_path, state)
        return {'status': report['status'], 'png_ready_seconds': png_ready,
                'candidate_and_comparison_ready_seconds': total, 'steps_seconds': report['steps_seconds'],
                'output': report['output'], 'comparison': report['comparison'], 'report': str(folder/'timing-report.json')}
    except Exception as error:
        state.update(status='failed', error=str(error), elapsed_seconds=time.perf_counter()-state['started_perf'])
        write_json(state_path, state)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='action', required=True)
    first = subparsers.add_parser('start')
    first.add_argument('--config', required=True)
    first.add_argument('--prompt', required=True)
    first.add_argument('--edit-input', required=True)
    first.add_argument('--tool-dir', required=True, help='PNG save root, snapshotted recursively; isolate simultaneous image jobs')
    first.add_argument('--job-dir', required=True)
    last = subparsers.add_parser('finish')
    last.add_argument('--job-dir', required=True)
    last.add_argument('--tool-started-unix', required=True, type=float)
    last.add_argument('--tool-ended-unix', required=True, type=float)
    args = parser.parse_args()
    try:
        print(json.dumps(start(args) if args.action == 'start' else finish(args), ensure_ascii=False))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
