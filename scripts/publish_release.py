"""Publish the validated package with the repository's GitHub Actions token."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from verify_release import ROOT, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('asset', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    metadata = verify(args.asset)
    tag = metadata['tag']
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Release tag must be a semantic version')
    notes = (ROOT/metadata['notes_file']).resolve()
    if not notes.is_relative_to(ROOT/'docs/releases') or not notes.is_file():
        raise ValueError('Release notes must be a checked-in release document')
    repository = os.environ['GITHUB_REPOSITORY']
    revision = os.environ['GITHUB_SHA']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Invalid Actions repository or commit')
    command = ['gh', 'release', 'create', tag, str(args.asset), '--repo', repository,
               '--target', revision, '--title', metadata['name'], '--notes-file', str(notes)]
    if metadata['prerelease']:
        command.append('--prerelease')
    if args.dry_run:
        print(json.dumps(command, ensure_ascii=False))
        return
    existing = subprocess.run(['gh', 'api', f'repos/{repository}/releases/tags/{tag}'], capture_output=True, text=True)
    if existing.returncode == 0:
        release = json.loads(existing.stdout)
        if release.get('draft') or not any(a['name'] == args.asset.name for a in release.get('assets', [])):
            raise RuntimeError('An incomplete release already exists; inspect it instead of overwriting it')
        print(f'{tag} is already published; existing tag, notes and assets are unchanged.')
        return
    if 'HTTP 404' not in existing.stderr:
        raise RuntimeError(existing.stderr.strip())
    subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
