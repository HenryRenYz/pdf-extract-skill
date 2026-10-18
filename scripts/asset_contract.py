"""Shared final-asset references; strings and {path, sha256} use one parser."""
from pathlib import Path
from source_identity import file_hash


def asset_reference(value):
    if isinstance(value, str) and value:
        return value, None
    if isinstance(value, dict) and isinstance(value.get('path'), str) and value['path']:
        return value['path'], value.get('sha256')
    raise ValueError('Asset reference requires a path string or path/sha256 object')


def resolve_asset(value, base, verify=True):
    raw, expected = asset_reference(value)
    path = (Path(base) / raw).expanduser().resolve()
    if verify and (not path.is_file() or expected and file_hash(path) != expected):
        raise ValueError('Selected asset missing or hash changed: ' + str(path))
    return path
