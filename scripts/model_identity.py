"""Freeze every weight/config/processor artifact used by a local OCR model."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
from source_identity import file_hash


def model_identity(directory):
    root = Path(directory).expanduser().resolve(strict=True)
    suffixes = {'.safetensors', '.bin', '.pt', '.pth', '.gguf', '.json', '.txt', '.model', '.tiktoken', '.py'}
    files = {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(root.rglob('*'))
             if p.is_file() and p.suffix in suffixes and '.cache' not in p.relative_to(root).parts
             and '__pycache__' not in p.parts}
    if 'config.json' not in files:
        raise ValueError('Local model requires config.json')
    # Explicitly reject a partial indexed download before inference or resume.
    for name in files:
        if name.endswith('.index.json'):
            index = json.loads((root / name).read_text())
            for shard in set(index.get('weight_map', {}).values()):
                if shard not in files:
                    raise ValueError('Missing indexed model weight: ' + shard)
    versions = {}
    for name in ('torch', 'transformers', 'mineru-vl-utils', 'Pillow'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    identity = {'files': files, 'runtime_versions': versions}
    identity['sha256'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return identity
