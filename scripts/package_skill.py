"""Build a portable skill ZIP containing only runtime skill resources."""
import argparse
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
skill = root / 'skills/restore-collectible-artwork'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=root / 'dist/restore-collectible-artwork-skill-local.zip')
args = parser.parse_args()
destination = args.output.resolve()
if destination.exists():
    raise SystemExit('Output exists; choose a new --output path to preserve previous packages')
destination.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(skill.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.md', '.py', '.txt', '.json', '.yaml', '.js', '.html'}:
            archive.write(path, path.relative_to(skill.parent))
print(destination)
