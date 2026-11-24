"""Verify that a release ZIP contains exactly the reviewed skill resources."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def verify(archive):
    metadata = json.loads((ROOT/'release.json').read_text(encoding='utf-8'))
    expected = metadata['skill_files_sha256']
    with zipfile.ZipFile(archive) as package:
        if len(package.namelist()) != len(expected) or set(package.namelist()) != set(expected):
            raise ValueError('Package contents differ from the reviewed release manifest')
        for name, expected_hash in expected.items():
            path = Path(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Invalid package path')
            content = package.read(name)
            if hashlib.sha256(content).hexdigest() != expected_hash:
                raise ValueError(f'Package resource differs: {name}')
            if content != (ROOT/'skills'/path).read_bytes():
                raise ValueError(f'Package differs from checked-out skill: {name}')
    return metadata


if __name__ == '__main__':
    verify(sys.argv[1])
    print('Release package matches every reviewed resource.')
