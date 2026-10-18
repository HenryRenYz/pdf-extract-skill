#!/usr/bin/env python3
"""Prepare explicitly reviewed front/back covers, preserving native PDF pages.

No OCR, model calls, page-role inference, resampling or reconstructed artwork.
The reviewed decision file selects image/title mode; image is the workflow
default, but a page is never classified as a cover merely by its position.
"""
import argparse
import copy
import json
import shutil
import tempfile
from pathlib import Path

from prepare_images import atomic_json, digest, select_pages

ROLES = {'front_cover', 'title_page', 'back_cover'}
MODES = {'image', 'title'}


def checked_review(review):
    if (not isinstance(review, dict) or review.get('status') != 'source_checked'
            or any(not isinstance(review.get(key), str) or not review[key].strip()
                   for key in ('reviewer', 'evidence', 'decision_source'))):
        raise ValueError('Cover role/mode requires a source_checked review with reviewer, evidence and decision_source')


def checked_metadata(metadata):
    """Only explicit, attributed title information; no guessed author/edition."""
    if not isinstance(metadata, dict) or 'title' not in metadata:
        raise ValueError('Title mode requires explicitly reviewed title metadata')
    allowed = {'title', 'subtitle', 'authors', 'edition', 'publisher', 'year'}
    if set(metadata) - allowed:
        raise ValueError('Unsupported title metadata fields: ' + ', '.join(sorted(set(metadata) - allowed)))
    for field, value in metadata.items():
        if (not isinstance(value, dict) or not isinstance(value.get('value'), str)
                or not value['value'].strip()):
            raise ValueError('Title metadata must have a nonempty value and field-level review: ' + field)
        checked_review(value.get('review'))


def check_records(records, selected):
    if not isinstance(records, list) or not records:
        raise ValueError('Explicit cover records are required')
    numbers = [r.get('page') if isinstance(r, dict) else None for r in records]
    if (any(type(n) is not int or n < 1 for n in numbers)
            or len(set(numbers)) != len(numbers) or sorted(numbers) != sorted(selected)):
        raise ValueError('Reviewed cover page list must exactly match --pages')
    for record in records:
        if record.get('role') not in ROLES or record.get('mode') not in MODES:
            raise ValueError('Explicit valid cover role and mode are required')
        checked_review(record.get('review'))
        if record['mode'] == 'title':
            checked_metadata(record.get('metadata'))


def load_cover_manifest(path, source_sha256, expected_pages):
    """Verify source/scope/assets; return physical-page -> copied record.

    Each result has an absolute ``asset_path`` plus the unchanged relative
    ``asset.path`` and checksum. Generated PDF assets remain a single native
    source page; source role/metadata are reviewer decisions, never OCR claims.
    """
    path = Path(path).expanduser().resolve(strict=True)
    data = json.loads(path.read_text())
    expected = sorted(expected_pages)
    if (data.get('schema_version') != 1 or data.get('status') != 'complete'
            or data.get('source', {}).get('pdf_sha256') != source_sha256
            or data.get('expected_pages') != expected):
        raise ValueError('Cover manifest source hash, status or expected page scope differs')
    selected = data.get('cover_pages')
    if not isinstance(selected, list) or not selected or not set(selected).issubset(expected):
        raise ValueError('Cover manifest includes pages outside the requested book scope')
    check_records(data.get('records'), selected)
    import pymupdf as fitz
    result = {}
    for original in data['records']:
        record = copy.deepcopy(original)
        asset = record.get('asset', {})
        relative = asset.get('path')
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError('Cover asset path must be relative to the manifest')
        target = (path.parent / relative).resolve(strict=True)
        if (not target.is_relative_to(path.parent) or not target.is_file()
                or digest(target) != asset.get('sha256') or target.suffix.lower() != '.pdf'):
            raise ValueError('Cover asset missing, escaped directory, changed or not native PDF')
        with fitz.open(target) as native:
            if len(native) != 1:
                raise ValueError('Cover asset must contain exactly one PDF page')
            page = native[0]
            geometry = record.get('source_page', {})
            actual = {'bbox_pdf_points': list(page.rect), 'media_box_pdf_points': list(page.mediabox),
                      'crop_box_pdf_points': list(page.cropbox), 'rotation': page.rotation}
            if any(actual[key] != geometry.get(key) for key in actual):
                raise ValueError('Cover asset page boxes/rotation differ from source record')
        record['asset_path'] = str(target)
        record['cover_manifest_path'] = str(path)
        record['cover_manifest_sha256'] = digest(path)
        record['source_pdf_sha256'] = source_sha256
        result[record['page']] = record
    return result


def minimal_cover_page(record, page_meta):
    """Build a traceable whole-page placeholder; its text is provenance, not OCR."""
    number = record['page']
    if page_meta.get('page') != number:
        raise ValueError('Cover record and prepared page metadata refer to different physical pages')
    width, height = page_meta.get('width'), page_meta.get('height')
    if (type(width) not in (int, float) or type(height) not in (int, float)
            or width <= 0 or height <= 0):
        raise ValueError('Prepared cover page requires positive source-image dimensions')
    bbox = [0, 0, width, height]
    descriptor = ('Whole source page retained; no OCR transcription was performed' if record['mode'] == 'image'
                  else 'Cover key metadata comes from field-by-field verification; no OCR transcription was performed')
    return {'page': number, 'printed_page': None,
            'blocks': [{'id': f'p{number:04d}-cover', 'type': 'figure', 'text': '[' + descriptor + ']',
                        'bbox': bbox, 'styles': [], 'evidence': 'source_checked',
                        'cover': copy.deepcopy(record),
                        'provenance': {'kind': 'reviewed_cover_page', 'transcription': False,
                                       'source_pdf_sha256': record.get('source_pdf_sha256'),
                                       'source_image_sha256': page_meta.get('original', {}).get('sha256'),
                                       'review': copy.deepcopy(record['review'])}}],
            'uncertainties': [], 'changes': [], 'viewed_images': [],
            'input_kind': 'reviewed_cover_without_ocr'}


def prepare(source, selected, expected, decisions, output):
    import pymupdf as fitz
    source = Path(source).expanduser().resolve(strict=True)
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError('Cover output already exists; choose a new directory')
    source_hash = digest(source)
    if decisions.get('source_sha256') != source_hash:
        raise ValueError('Cover decisions do not match the current source PDF hash')
    check_records(decisions.get('records'), selected)
    if not set(selected).issubset(expected):
        raise ValueError('Selected cover pages are outside expected book scope')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.covers-', dir=output.parent))
    try:
        records = []
        with fitz.open(source) as document:
            if not expected or max(expected) > len(document):
                raise ValueError('Expected page scope extends beyond the source PDF')
            for decision in sorted(decisions['records'], key=lambda r: r['page']):
                number = decision['page']
                page = document[number - 1]
                geometry = {'bbox_pdf_points': list(page.rect), 'media_box_pdf_points': list(page.mediabox),
                            'crop_box_pdf_points': list(page.cropbox), 'rotation': page.rotation,
                            'display_width_pt': page.rect.width, 'display_height_pt': page.rect.height}
                target = staging / f'p{number:04d}-{decision["role"]}.pdf'
                with fitz.open() as single:
                    single.insert_pdf(document, from_page=number - 1, to_page=number - 1)
                    single.save(target, garbage=4, deflate=True)
                with fitz.open(target) as extracted:
                    # Independent pixel equality at 72 dpi is validation only;
                    # the delivered asset itself remains native PDF.
                    original_pixels = page.get_pixmap(alpha=False)
                    extracted_pixels = extracted[0].get_pixmap(alpha=False)
                    if ((original_pixels.width, original_pixels.height) != (extracted_pixels.width, extracted_pixels.height)
                            or original_pixels.samples != extracted_pixels.samples):
                        raise ValueError(f'Native page extraction changed visible content: {number}')
                record = copy.deepcopy(decision)
                record.update({'source_page': geometry,
                               'asset': {'path': target.name, 'sha256': digest(target), 'kind': 'native_single_page_pdf'},
                               'extraction': {'operation': 'native_pdf_page_copy', 'rasterized': False,
                                              'verification': '72dpi_visible_pixels_identical'}})
                records.append(record)
            manifest = {'schema_version': 1, 'status': 'complete',
                        'source': {'pdf_path': str(source), 'pdf_sha256': source_hash, 'total_pages': len(document)},
                        'expected_pages': sorted(expected), 'cover_pages': sorted(selected), 'records': records}
        if digest(source) != source_hash:
            raise ValueError('Source PDF changed during extraction')
        atomic_json(staging / 'covers.json', manifest)
        load_cover_manifest(staging / 'covers.json', source_hash, expected)
        staging.replace(output)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-pdf', type=Path, required=True)
    parser.add_argument('--pages', required=True, help='Explicitly reviewed physical pages, e.g. 1,2')
    parser.add_argument('--expected-pages', required=True, help='Requested book scope, e.g. 1-50')
    parser.add_argument('--review-json', type=Path, required=True, help='source_sha256 and explicit reviewed records')
    parser.add_argument('--output', type=Path, required=True, help='New directory containing covers.json and native PDF pages')
    args = parser.parse_args()
    try:
        manifest = prepare(args.source_pdf, select_pages(args.pages), select_pages(args.expected_pages),
                           json.loads(args.review_json.expanduser().read_text()), args.output)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps({'cover_pages': manifest['cover_pages'], 'modes': [r['mode'] for r in manifest['records']],
                      'source_sha256': manifest['source']['pdf_sha256'], 'model_calls': 0, 'rasterized': False}))


if __name__ == '__main__':
    main()
