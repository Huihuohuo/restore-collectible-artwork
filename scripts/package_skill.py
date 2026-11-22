"""Build a portable skill ZIP containing only versioned skill resources."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
skill = root / 'skills/restore-collectible-artwork'
destination = root / 'dist/restore-collectible-artwork-skill.zip'
destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(skill.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.md', '.py', '.txt', '.json', '.yaml', '.js'}:
            archive.write(path, path.relative_to(skill.parent))
print(destination)
