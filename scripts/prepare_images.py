#!/usr/bin/env python3
"""Prepare traceable PDF/page images without OCR or model calls."""
import argparse
import hashlib
import json
import math
import shutil
import time
from pathlib import Path

from PIL import Image
from source_identity import source_record, numbered_images


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, data):
    """Publish one complete JSON snapshot, never a half-written reader view."""
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.replace(path)


def manifest_errors(manifest, expected_pages=None):
    """Check preparation state and scope; no claims about image/content quality."""
    errors = []
    numbers = [p.get('page') for p in manifest.get('pages', [])]
    if any(type(n) is not int or n < 1 for n in numbers) or len(set(numbers)) != len(numbers):
        errors.append('Invalid/duplicate physical pages in image manifest')
    status = manifest.get('preparation_status')
    requested = manifest.get('requested_pages')
    if status is not None and status != 'complete':
        errors.append(f'Image preparation is not complete: {status}')
    if status == 'complete':
        if (not isinstance(requested, list) or not requested
                or any(type(n) is not int or n < 1 for n in requested)
                or len(set(requested)) != len(requested)
                or sorted(requested) != sorted(numbers)
                or manifest.get('completed_pages') != sorted(numbers)):
            errors.append('Complete image manifest does not match its requested/completed page scope')
    elif manifest.get('schema_version') == 1:
        # Historical prepare_images published partial schema-1 manifests too.
        # Only the terminal write included preparation_seconds.
        elapsed = manifest.get('preparation_seconds')
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            errors.append('Legacy image manifest has no completed-preparation evidence')
    if expected_pages is not None and sorted(numbers) != sorted(expected_pages):
        errors.append(f'Image manifest page scope differs from requested scope: expected {expected_pages}, found {numbers}')
    return errors


def select_pages(spec):
    pages = set()
    for item in spec.split(','):
        limits = item.strip().split('-')
        if len(limits) == 1:
            start = end = int(limits[0])
        elif len(limits) == 2:
            start, end = map(int, limits)
        else:
            raise ValueError('Use page numbers/ranges such as 1-25,32,40-43')
        if start < 1 or end < start:
            raise ValueError('Physical pages must be positive, ascending ranges')
        pages.update(range(start, end + 1))
    return sorted(pages)


def starts(length, size, overlap):
    if length <= size:
        return [0]
    if overlap >= size:
        raise ValueError('Overlap must be smaller than each tiled dimension')
    positions, origin = [], 0
    while True:
        positions.append(origin)
        if origin + size >= length:
            return positions
        origin += size - overlap


def file_record(path, root, kind, bbox=None):
    with Image.open(path) as img:
        width, height = img.size
    record = {'path': path.relative_to(root).as_posix(), 'kind': kind,
              'width': width, 'height': height, 'sha256': digest(path)}
    if bbox is not None:
        record['source_bbox'] = bbox
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--pdf', type=Path)
    source.add_argument('--pages-dir', type=Path, help='Numbered PNG pages, e.g. 0001.png')
    parser.add_argument('--pages', required=True, help='1-based physical pages, e.g. 1-25,32')
    parser.add_argument('--output', type=Path, required=True, help='New directory; never overwrite')
    parser.add_argument('--dpi', type=float, default=150)
    parser.add_argument('--max-pixels', type=int, required=True, help='Verified backend image-area budget')
    parser.add_argument('--max-edge', type=int, default=2048)
    parser.add_argument('--tile-height', type=int, help='Optional strip-height ceiling')
    parser.add_argument('--overlap', type=int, default=128)
    parser.add_argument('--overview-edge', type=int, default=1024)
    parser.add_argument('--crop-json', type=Path, help='Extra crops: [{page: 1, bbox: [x0,y0,x1,y1]}]')
    args = parser.parse_args()
    if min(args.max_pixels, args.max_edge, args.overview_edge, args.dpi) <= 0 or args.overlap < 0:
        parser.error('Sizes/dpi must be positive; overlap must be nonnegative')
    if args.tile_height is not None and args.tile_height <= 0:
        parser.error('tile-height must be positive')
    pages = select_pages(args.pages)
    root = args.output.expanduser().resolve()
    if root.exists():
        parser.error('Output already exists; choose a new directory to preserve provenance')
    document, inputs, source_meta = None, {}, {}
    if args.pdf:
        import pymupdf as fitz
        path = args.pdf.expanduser().resolve(strict=True)
        document = fitz.open(path)
        if max(pages) > len(document):
            parser.error(f'PDF contains only {len(document)} physical pages')
        source_meta = {**source_record(path),
                       'total_pages': len(document), 'render_dpi': args.dpi}
    else:
        directory = args.pages_dir.expanduser().resolve(strict=True)
        inputs = numbered_images(directory)
        missing = set(pages) - inputs.keys()
        if missing:
            parser.error(f'Missing page images: {sorted(missing)}')
        source_meta = source_record(directory)
    crops = json.loads(args.crop_json.read_text()) if args.crop_json else []
    if not isinstance(crops, list) or any(c.get('page') not in pages for c in crops):
        parser.error('Extra crops must be a list of selected page records')
    root.mkdir(parents=True)
    started = time.perf_counter()
    manifest = {'schema_version': 2, 'source': source_meta,
                'preparation_status': 'preparing', 'requested_pages': pages,
                'completed_pages': [],
                'coordinate_system': 'source-image pixels; [x0,y0,x1,y1), origin top-left',
                'image_budget': {'max_pixels': args.max_pixels, 'max_edge': args.max_edge,
                                 'overlap': args.overlap}, 'pages': []}
    atomic_json(root / 'progress.json', manifest)
    try:
        for number in pages:
            folder = root / 'input' / f'{number:04d}'
            folder.mkdir(parents=True)
            original = folder / 'original.png'
            if document is not None:
                page = document[number - 1]
                page.get_pixmap(matrix=fitz.Matrix(args.dpi / 72, args.dpi / 72),
                                alpha=False).save(original)
            else:
                shutil.copyfile(inputs[number], original)
            with Image.open(original) as opened:
                image = opened.convert('RGB')
            width, height = image.size
            record = {'page': number, 'width': width, 'height': height,
                      'printed_page': None, 'original': file_record(original, root, 'original'),
                      'images': []}
            overview = image.copy()
            # Keep overview below the same budget; never enlarge it.
            factor = min(1, args.overview_edge / max(width, height),
                         math.sqrt(args.max_pixels / (width * height)),
                         args.max_edge / max(width, height))
            overview.thumbnail((max(1, int(width * factor)), max(1, int(height * factor))),
                               Image.Resampling.LANCZOS)
            overview_path = folder / 'overview.png'
            overview.save(overview_path)
            record['images'].append(file_record(overview_path, root, 'overview', [0, 0, width, height]))
            tile_width = min(width, args.max_edge, args.max_pixels)
            tile_height = min(height, args.max_edge, args.max_pixels // tile_width,
                              args.tile_height or height)
            index = 0
            for y in starts(height, tile_height, args.overlap):
                for x in starts(width, tile_width, args.overlap):
                    index += 1
                    bbox = [x, y, min(width, x + tile_width), min(height, y + tile_height)]
                    path = folder / f'tile-{index:03d}.png'
                    image.crop(bbox).save(path)
                    record['images'].append(file_record(path, root, 'tile', bbox))
            for index, crop in enumerate((c for c in crops if c['page'] == number), 1):
                bbox = crop['bbox']
                if (len(bbox) != 4 or any(type(v) is not int for v in bbox)
                        or not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height)):
                    raise ValueError(f'Invalid source bbox on page {number}: {bbox}')
                if ((bbox[2]-bbox[0]) * (bbox[3]-bbox[1]) > args.max_pixels
                        or max(bbox[2]-bbox[0], bbox[3]-bbox[1]) > args.max_edge):
                    raise ValueError('Extra crop exceeds backend budget; split it into smaller regions')
                path = folder / f'crop-{index:03d}.png'
                image.crop(bbox).save(path)
                record['images'].append(file_record(path, root, 'crop', bbox))
            manifest['pages'].append(record)
            manifest['completed_pages'].append(number)
            # A complete assigned page may be consumed while the whole book is
            # preparing. Its scope never pretends to be the full request.
            ready = {key: value for key, value in manifest.items() if key != 'pages'}
            ready.update(preparation_status='complete', requested_pages=[number],
                         completed_pages=[number], source_requested_pages=pages,
                         preparation_seconds=time.perf_counter() - started,
                         pages=[record])
            atomic_json(root / f'page-{number:04d}.json', ready)
            atomic_json(root / 'progress.json', manifest)
        manifest['preparation_seconds'] = time.perf_counter() - started
        manifest['preparation_status'] = 'complete'
        atomic_json(root / 'progress.json', manifest)
        atomic_json(root / 'manifest.json', manifest)
    except BaseException as exc:
        manifest.update(preparation_status='failed', error_type=type(exc).__name__,
                        preparation_seconds=time.perf_counter() - started)
        atomic_json(root / 'progress.json', manifest)
        raise
    finally:
        if document is not None:
            document.close()
    print(json.dumps({'pages': len(pages), 'manifest': str(root / 'manifest.json'),
                      'seconds': manifest['preparation_seconds']}))


if __name__ == '__main__':
    main()
