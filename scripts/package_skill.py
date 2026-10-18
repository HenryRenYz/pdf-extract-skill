#!/usr/bin/env python3
"""Distribute only maintained runtime resources, excluding inputs and experiments."""
import argparse
import json
from pathlib import Path
import zipfile
from source_identity import file_hash
from latex_template import load_template


def runtime_files(root):
    root = Path(root).resolve()
    files = [root / 'SKILL.md', root / 'pyproject.toml']
    files.extend(root / name for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md') if (root / name).is_file())
    for directory, suffixes in {'scripts': {'.py'}, 'references': {'.md'},
                               'agents': {'.yaml', '.yml'}, 'examples': {'.py'},
                               'assets': {'.py', '.txt', '.json', '.tex', '.sty', '.cls', '.ttf', '.otf', '.ttc'}}.items():
        files.extend(p for p in (root / directory).rglob('*')
                     if p.is_file() and p.suffix.lower() in suffixes and '__pycache__' not in p.parts)
    # A user template may require bibliography files, PDF artwork, extensionless
    # licenses, or other resources outside the historical asset suffix list.
    for manifest in (root/'assets').rglob('template.json'):
        project = load_template(manifest.parent)
        files.extend(manifest.parent/name for name in project['resources'])
    for path in files:
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError('Runtime resources cannot escape the skill via symlinks')
    return sorted(set(files))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    if output.exists():
        parser.error('Archive exists; choose a new release path')
    if any(output.is_relative_to(root / part) for part in ('scripts', 'references', 'assets', 'agents')):
        parser.error('Archive cannot be written into runtime resource directories')
    files = runtime_files(root)
    manifest = {'schema_version': 1, 'files': {str(p.relative_to(root)): file_hash(p) for p in files}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root))
        archive.writestr('skill-manifest.json', json.dumps(manifest, indent=2))
    print(json.dumps({'output': str(output), 'files': len(files), 'sha256': file_hash(output)}))


if __name__ == '__main__':
    main()
