#!/usr/bin/env python3
"""Extract PDF text-layer drafts for source-image proofreading, never certify OCR."""
import argparse
import json
import time
from pathlib import Path
from prepare_images import atomic_json, digest, manifest_errors, select_pages
from source_identity import source_digest, manifest_source_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-pdf', type=Path, required=True)
    parser.add_argument('--prepared-dir', type=Path, required=True)
    parser.add_argument('--pages', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import pymupdf as fitz
    source = args.source_pdf.expanduser().resolve(strict=True)
    prepared = args.prepared_dir.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    pages = select_pages(args.pages)
    manifest = json.loads((prepared / 'manifest.json').read_text())
    checksum = source_digest(source)
    if manifest_errors(manifest) or manifest_source_hash(manifest) != checksum:
        parser.error('Prepared manifest/current source identity differs')
    by_page = {p['page']: p for p in manifest['pages']}
    if not set(pages) <= by_page.keys():
        parser.error('Native draft pages must be inside the prepared scope')
    if output.exists():
        parser.error('Native draft output exists; use a new revision')
    output.mkdir(parents=True)
    contract = {'kind': 'native_text', 'source_sha256': checksum,
                'adapter_sha256': digest(Path(__file__)), 'pymupdf_version': fitz.VersionBind}
    atomic_json(output / 'contract.json', contract)
    records = []
    with fitz.open(source) as document:
        for number in pages:
            started = time.perf_counter()
            meta = by_page[number]
            image = (prepared / meta['original']['path']).resolve(strict=True)
            if not image.is_relative_to(prepared) or digest(image) != meta['original']['sha256']:
                raise ValueError('Prepared native draft image changed')
            page = document[number - 1]
            raw = page.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES, sort=True)
            texts = ['\n'.join(''.join(s['text'] for s in line['spans']) for line in b['lines'])
                     for b in raw['blocks'] if b['type'] == 0]
            text = '\n\n'.join(texts)
            # Preserve raw text, including problematic glyphs, as a draft only.
            flags = []
            if not text.strip():
                flags.append('no_native_text; source images determine blank versus scanned content')
            if any(ord(c) < 32 and c not in '\n\r\t' or c == '\ufffd' for c in text):
                flags.append('control_or_replacement_characters')
            if any(str(row[2]).lower() == 'type3' for row in page.get_fonts()):
                flags.append('type3_fonts_require_visual_review')
            target = output / f'{number:04d}'
            target.mkdir()
            atomic_json(target / 'raw-blocks.json', {'blocks': raw['blocks'],
                        'coordinates': 'Unrotated PyMuPDF page points, top-left origin; apply pdf_to_prepared_matrix for original-image pixels',
                        'page_rect': list(page.rect), 'prepared_size_px': [meta['width'], meta['height']],
                        'cropbox': list(page.cropbox), 'mediabox': list(page.mediabox),
                        'rotation_matrix': list(page.rotation_matrix),
                        'pdf_to_prepared_matrix': list(page.rotation_matrix * fitz.Matrix(
                            meta['width'] / page.rect.width, meta['height'] / page.rect.height)),
                        'rotation': page.rotation, 'quality_flags': flags})
            (target / 'original.md').write_text(text, encoding='utf-8')
            atomic_json(target / 'quality.json', {'source_accuracy': 'unreviewed', 'quality_flags': flags,
                        'native_characters': len(text), 'requires_visual_review': True})
            record = {'page': number, 'status': 'produced', 'source_sha256': checksum,
                      'image_sha256': meta['original']['sha256'], 'contract': contract,
                      'elapsed_seconds': time.perf_counter() - started,
                      'outputs': {p.name: digest(p) for p in target.iterdir() if p.is_file()}}
            atomic_json(target / 'page-ready.json', record)
            records.append(record)
    if source_digest(source) != checksum:
        raise ValueError('PDF changed during native extraction')
    atomic_json(output / 'report.json', {'requested_pages': pages, 'all_produced': True,
                'records': records, 'quality': 'native draft only; formulas/order/styles require source-image proofreading'})
    print(json.dumps({'status': 'produced', 'pages': pages, 'model_calls': 0}))


if __name__ == '__main__':
    main()
