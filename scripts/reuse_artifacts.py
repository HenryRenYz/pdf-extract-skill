"""Explicit source-bound reuse of validated page artifacts across execution stages."""
import json
import shutil
from pathlib import Path
from prepare_images import atomic_json, digest
from source_identity import manifest_source_hash
from validate_outputs import validate


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Reusable artifact inventory cannot contain symlinks')
        if path.is_file():
            result[str(path.relative_to(root))] = digest(path)
    return result


def adopt_page(roots, destination, page, page_record, source_hash, fingerprint):
    candidates = []
    for root in roots:
        root = Path(root).resolve(strict=True)
        contract = json.loads((root/'contract.json').read_text())
        if contract.get('source_pdf_sha256') != source_hash:
            raise ValueError('Reuse source differs')
        if page not in contract.get('owned_pages', contract.get('expected_pages', [])):
            continue
        attempts = sorted((root/'pages'/f'{page:04d}').glob('attempt-*'))
        if attempts:
            candidates.append((root, contract, attempts[-1]))
    if len(candidates) > 1:
        raise ValueError('Ambiguous reusable page producers')
    if not candidates:
        return None
    root, contract, previous = candidates[0]
    checkpoint = previous/'checkpoint.json'
    prior = json.loads(checkpoint.read_text())
    if prior.get('status') != 'validated' or prior.get('exit_code') != 0 or prior.get('timed_out'):
        raise ValueError('Only successful validated attempts can be reused; review failed calls separately')
    hashes = prior.get('file_hashes')
    if not hashes or inventory(previous) != {**hashes, 'checkpoint.json': digest(checkpoint)}:
        raise ValueError('Reusable artifact inventory changed')
    manifest = json.loads((previous/'input/manifest.json').read_text())
    old_pages = manifest.get('pages', [])
    if manifest_source_hash(manifest) != source_hash or len(old_pages) != 1 or old_pages[0].get('page') != page:
        raise ValueError('Reusable page/source differs')
    def image_identity(meta):
        return [(x['sha256'], x['width'], x['height']) for x in [meta['original'], *meta['images']]]
    if image_identity(old_pages[0]) != image_identity(page_record):
        raise ValueError('Reusable prepared images/geometry differ')
    if not validate(previous/'input/manifest.json', previous/'output', require_markdown=not prior.get('json_only', False))['structure_ok']:
        raise ValueError('Reusable page fails the current semantic schema')
    if destination.exists():
        raise ValueError('Reuse destination exists')
    destination.mkdir(parents=True)
    for folder in ('input', 'output'):
        shutil.copytree(previous/folder, destination/folder)
    provenance = destination/'provenance'
    provenance.mkdir()
    shutil.copyfile(checkpoint, provenance/'producer-checkpoint.json')
    shutil.copyfile(root/'contract.json', provenance/'producer-contract.json')
    # Relocate only known source-image aliases. Preserve exact old output bytes
    # as provenance so no content/style or model attribution can be rewritten.
    output = destination/'output'/f'{page:04d}.json'
    shutil.copyfile(output, provenance/'producer-output.json')
    data = json.loads(output.read_text())
    aliases = {}
    for image in [old_pages[0]['original'], *old_pages[0]['images']]:
        value = image['path']
        for form in (value, 'input/'+value, str((previous/'input'/value).resolve())):
            aliases[form] = value
    if any(value not in aliases for value in data.get('viewed_images', [])):
        raise ValueError('Reusable output claims unassigned viewed images')
    data['viewed_images'] = [aliases[value] for value in data['viewed_images']]
    atomic_json(output, data)
    report = validate(destination/'input/manifest.json', destination/'output', require_markdown=not prior.get('json_only', False))
    atomic_json(destination/'validation.json', report)
    if not report['structure_ok']:
        raise ValueError('Relocated page fails validation')
    record = {'page': page, 'attempt': 1, 'fingerprint': fingerprint,
              'status': 'validated', 'exit_code': 0, 'timed_out': False,
              'backend': prior.get('backend'), 'model': prior.get('model'),
              'actual_models': prior.get('actual_models', []), 'json_only': prior.get('json_only', False),
              'model_call_started': False, 'model_invocations': 0, 'reused': True,
              'reuse': {'producer_contract_sha256': digest(root/'contract.json'),
                        'producer_checkpoint_sha256': digest(checkpoint),
                        'producer_output_sha256': digest(provenance/'producer-output.json'),
                        'source_run': str(root), 'source_attempt': str(previous),
                        'note': 'Existing transcription reused; current mode/backend does not imply new model processing or source review'},
              'quality': 'current structural validation; no new accuracy or proofreading endorsement'}
    record['file_hashes'] = inventory(destination)
    atomic_json(destination/'checkpoint.json', record)
    return record
