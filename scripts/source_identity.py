"""Path-independent document identities for PDFs and numbered page images."""
import hashlib
import json
from pathlib import Path


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def numbered_images(directory):
    found = {}
    for path in Path(directory).iterdir():
        if path.is_file() and path.suffix.lower() == '.png' and path.stem.isdigit():
            number = int(path.stem)
            if number < 1 or number in found:
                raise ValueError('Page image numbers must be positive and unique')
            found[number] = path
    if not found:
        raise ValueError('No numbered PNG pages in source directory')
    return dict(sorted(found.items()))


def source_record(path):
    path = Path(path).expanduser().resolve(strict=True)
    if path.is_file():
        checksum = file_hash(path)
        return {'kind': 'pdf', 'sha256': checksum, 'pdf_sha256': checksum, 'pdf_path': str(path)}
    records = [{'page': n, 'sha256': file_hash(p)} for n, p in numbered_images(path).items()]
    checksum = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'kind': 'image_collection', 'sha256': checksum, 'pages_directory': str(path),
            'image_files': records, 'pixel_operation': 'native PNG copy'}


def source_digest(path):
    return source_record(path)['sha256']


def manifest_source_hash(manifest):
    source = manifest.get('source', {})
    values = {v for v in (source.get('sha256'), source.get('pdf_sha256'),
                          manifest.get('source_sha256')) if v is not None}
    if len(values) > 1:
        raise ValueError('Conflicting document source hashes')
    return next(iter(values), None)
