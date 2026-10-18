#!/usr/bin/env python3
"""Import existing engine drafts without installing or impersonating that engine."""
import argparse
import json
import shutil
from pathlib import Path
from prepare_images import atomic_json, manifest_errors
from source_identity import source_digest, manifest_source_hash, file_hash
from runtime_contract import stage_identity


def import_drafts(source, prepared, specification, output):
    source, prepared, specification, output = map(lambda p: Path(p).resolve(), (source, prepared, specification, output))
    manifest = json.loads((prepared/'manifest.json').read_text())
    checksum = source_digest(source)
    if manifest_errors(manifest) or manifest_source_hash(manifest) != checksum:
        raise ValueError('Prepared source/manifest differs')
    data = json.loads(specification.read_text())
    if data.get('schema_version') != 1 or data.get('source_sha256') != checksum:
        raise ValueError('Draft import schema/source identity differs')
    producer = data.get('producer')
    if not isinstance(producer, dict) or not all(isinstance(producer.get(k), str) and producer[k].strip() for k in ('name', 'version')):
        raise ValueError('Draft producer name and version are required')
    records = data.get('pages')
    if not isinstance(records, list) or not records or len({r['page'] for r in records}) != len(records):
        raise ValueError('Draft import requires unique pages')
    by_page = {p['page']: p for p in manifest['pages']}
    verified = []
    for row in records:
        page = by_page.get(row.get('page'))
        if page is None or row.get('image_sha256') != page['original']['sha256']:
            raise ValueError('Draft page/image identity differs')
        image = (prepared/page['original']['path']).resolve(strict=True)
        if not image.is_relative_to(prepared) or file_hash(image) != row['image_sha256']:
            raise ValueError('Prepared draft image changed')
        assets = {}
        for key in ('text', 'raw_blocks'):
            asset = row.get(key, {})
            relative = Path(asset.get('path', ''))
            path = (specification.parent/relative).resolve(strict=True)
            if relative.is_absolute() or not path.is_relative_to(specification.parent) or not path.is_file() or file_hash(path) != asset.get('sha256'):
                raise ValueError('Draft assets must be hash-bound files within import directory')
            # Raw blocks retain engine-native coordinates/schema. They are not
            # accepted as canonical semantic blocks until source proofreading.
            if key == 'text':
                path.read_text(encoding='utf-8')
            else:
                json.loads(path.read_text())
            assets[key] = path
        verified.append((row, assets))
    if output.exists():
        raise ValueError('Draft output exists; choose a new revision')
    output.mkdir(parents=True)
    contract = {'kind': 'imported_draft', 'source_sha256': checksum, 'producer': producer,
                'import_spec_sha256': file_hash(specification), 'adapter': stage_identity([Path(__file__)]),
                'quality': 'unreviewed engine draft; provenance declaration is not accuracy verification'}
    atomic_json(output/'contract.json', contract)
    for row, assets in verified:
        target = output/f'{row["page"]:04d}'
        target.mkdir()
        shutil.copyfile(assets['text'], target/'original.md')
        shutil.copyfile(assets['raw_blocks'], target/'raw-blocks.json')
        if (file_hash(target/'original.md') != row['text']['sha256']
                or file_hash(target/'raw-blocks.json') != row['raw_blocks']['sha256']):
            raise ValueError('Draft asset changed while importing; no ready receipt published')
        atomic_json(target/'page-ready.json', {'status': 'produced', 'page': row['page'],
                    'source_sha256': checksum, 'image_sha256': row['image_sha256'], 'contract': contract,
                    'outputs': {p.name: file_hash(p) for p in target.iterdir()}})
    return {'pages': [r['page'] for r in records], 'model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'prepared-dir', 'specification', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(import_drafts(args.source, args.prepared_dir, args.specification, args.output)))


if __name__ == '__main__':
    main()
