#!/usr/bin/env python3
"""Check output structure and provenance. This is NOT a content-accuracy evaluation."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import unquote, urlparse
from prepare_images import manifest_errors, select_pages
from source_identity import manifest_source_hash

EVIDENCE = {'unreviewed', 'source_checked', 'uncertain'}
STYLE_EVIDENCE = EVIDENCE | {'rule_inferred'}
from semantic_contract import TYPES, STYLES, validate_semantic_block


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def valid_bbox(box, width, height):
    return (isinstance(box, list) and len(box) == 4
            and all(type(v) in (float, int) and math.isfinite(v) for v in box)
            and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)


def choice(value, values):
    return isinstance(value, str) and value in values


def validate(manifest_path, output, expected_pages=None, require_markdown=True):
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    manifest = json.loads(manifest_path.read_text())
    root = manifest_path.parent
    errors, warnings, pages, seen_ids, seen_pages = [], [], [], set(), set()
    errors.extend(manifest_errors(manifest, expected_pages))
    if 'preparation_status' not in manifest:
        warnings.append('Legacy/static manifest: original requested scope unverified unless expected_pages is supplied')
    style_evidence_counts = {kind: 0 for kind in sorted(STYLE_EVIDENCE)}
    for page in manifest['pages']:
        number = page['page']
        if number in seen_pages:
            errors.append(f'Duplicate manifest page {number}')
        seen_pages.add(number)
        prefix = f'page {number}'
        page_errors, page_warnings = [], []
        inferred_count = 0
        records = [page['original']] + page['images']
        for record in records:
            path = (root / record['path']).resolve()
            if not path.is_relative_to(root):
                page_errors.append(f'{prefix}: source path outside manifest directory')
            elif not path.is_file() or digest(path) != record['sha256']:
                page_errors.append(f'{prefix}: source missing/hash changed: {record["path"]}')
        md_path, json_path = output / f'{number:04d}.md', output / f'{number:04d}.json'
        if require_markdown and (not md_path.is_file() or not md_path.read_text().strip()):
            page_errors.append(f'{prefix}: missing/empty Markdown')
        elif require_markdown:
            text = md_path.read_text()
            images = re.findall(r'!\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)', text)
            images += re.findall(r'<img\b[^>]*\bsrc=[\"\x27]([^\"\x27]+)', text, flags=re.I)
            for image in images:
                image = image.strip('<>')
                parsed = urlparse(image)
                if parsed.scheme or parsed.netloc:
                    page_warnings.append(f'{prefix}: remote image not checked: {parsed.scheme or "//"}')
                elif not (md_path.parent / unquote(parsed.path)).is_file():
                    page_errors.append(f'{prefix}: missing linked image: {image}')
        data = None
        if not json_path.is_file():
            page_errors.append(f'{prefix}: missing JSON')
        else:
            try:
                data = json.loads(json_path.read_text())
                if not isinstance(data, dict):
                    raise ValueError('page JSON must be an object')
            except (ValueError, TypeError) as exc:
                page_errors.append(f'{prefix}: invalid JSON: {exc}')
        if isinstance(data, dict):
            if type(data.get('page')) is not int or data['page'] != number:
                page_errors.append(f'{prefix}: physical page mismatch')
            if 'printed_page' not in data or (data['printed_page'] is not None
                    and type(data['printed_page']) not in (str, int)):
                page_errors.append(f'{prefix}: invalid/missing printed_page')
            for field in ('blocks', 'uncertainties', 'changes', 'viewed_images'):
                if not isinstance(data.get(field), list):
                    page_errors.append(f'{prefix}: {field} must be a list')
            block_ids = set()
            for block in data.get('blocks', []) if isinstance(data.get('blocks'), list) else []:
                if not isinstance(block, dict):
                    page_errors.append(f'{prefix}: invalid block object')
                    continue
                identifier = block.get('id')
                if not isinstance(identifier, str) or not identifier or identifier in seen_ids:
                    page_errors.append(f'{prefix}: missing/duplicate block ID')
                else:
                    seen_ids.add(identifier)
                    block_ids.add(identifier)
                if not choice(block.get('type'), TYPES) or not isinstance(block.get('text'), str):
                    page_errors.append(f'{prefix}: invalid block type/text: {identifier}')
                elif any(ord(c) < 32 and c not in '\n\r\t' or ord(c) == 127 for c in block['text']):
                    controls = sorted({f'U+{ord(c):04X}' for c in block['text']
                                       if ord(c) < 32 and c not in '\n\r\t' or ord(c) == 127})
                    page_errors.append(f'{prefix}: forbidden control characters in text: {identifier}: {controls}; check escaped LaTeX')
                if not choice(block.get('evidence'), EVIDENCE):
                    page_errors.append(f'{prefix}: invalid block evidence: {identifier}')
                if 'bbox' not in block:
                    page_errors.append(f'{prefix}: missing bbox: {identifier}')
                elif block['bbox'] is None:
                    page_warnings.append(f'{prefix}: block has no region: {identifier}')
                elif not valid_bbox(block['bbox'], page['width'], page['height']):
                    page_errors.append(f'{prefix}: out-of-bounds bbox: {identifier}')
                page_errors.extend(f'{prefix}: {identifier}: {error}' for error in validate_semantic_block(
                    block, manifest_source_hash(manifest), page['original']['sha256']))
                styles = block.get('styles')
                if not isinstance(styles, list):
                    page_errors.append(f'{prefix}: styles must be a list: {identifier}')
                    continue
                for style in styles:
                    if (not isinstance(style, dict) or not choice(style.get('kind'), STYLES)
                            or not choice(style.get('evidence'), STYLE_EVIDENCE)
                            or type(style.get('start')) is not int or type(style.get('end')) is not int
                            or not 0 <= style['start'] < style['end'] <= len(block['text'] if isinstance(block.get('text'), str) else '')):
                        page_errors.append(f'{prefix}: invalid style span: {identifier}')
                        continue
                    style_evidence_counts[style['evidence']] += 1
                    if style['evidence'] == 'rule_inferred':
                        inferred_count += 1
                        if any(not isinstance(style.get(key), str) or not style[key].strip()
                               for key in ('rule_id', 'rule_version')):
                            page_errors.append(f'{prefix}: inferred style missing rule ID/version: {identifier}')
            if data.get('page_status') == 'blank':
                if data.get('blocks') or not isinstance(data.get('blank_reason'), str) or not data['blank_reason'].strip():
                    page_errors.append(f'{prefix}: blank page requires no blocks and an explicit blank_reason')
            elif data.get('page_status', 'content') != 'content':
                page_errors.append(f'{prefix}: unknown page_status')
            elif not block_ids:
                page_errors.append(f'{prefix}: no identified blocks')
            for field in ('uncertainties', 'changes'):
                items = data.get(field)
                for item in items if isinstance(items, list) else []:
                    if (not isinstance(item, dict) or item.get('block_id') not in block_ids
                            or not isinstance(item.get('reason'), str) or not item['reason'].strip()
                            or 'bbox' not in item):
                        page_errors.append(f'{prefix}: invalid {field} record')
                        continue
                    if item['bbox'] is not None and not valid_bbox(item['bbox'], page['width'], page['height']):
                        page_errors.append(f'{prefix}: invalid {field} bbox')
                    if field == 'changes' and any(not isinstance(item.get(key), str) for key in ('before', 'after')):
                        page_errors.append(f'{prefix}: change missing before/after')
            viewed = data.get('viewed_images')
            if isinstance(viewed, list):
                expected = {image['path'] for image in page['images']}
                assigned = expected | {page['original']['path']}
                if any(not isinstance(value, str) for value in viewed):
                    page_errors.append(f'{prefix}: invalid viewed_images paths')
                else:
                    # A CLI works at job/ while its manifest lives at job/input/.
                    # Accept either documented relative namespace, without
                    # treating a path to another image as equivalent evidence.
                    reported = set()
                    for value in viewed:
                        if value in expected:
                            reported.add(value)
                        elif value.startswith('input/') and value[6:] in assigned and root.name == 'input':
                            reported.add(value[6:])
                        elif Path(value).is_absolute() and Path(value).resolve().is_relative_to(root):
                            reported.add(Path(value).resolve().relative_to(root).as_posix())
                        else:
                            reported.add(value)
                    if expected - reported:
                        page_errors.append(f'{prefix}: reported image coverage missing: {sorted(expected-reported)}')
                    if reported - expected - {page['original']['path']}:
                        page_errors.append(f'{prefix}: reported unassigned image paths')
            if data.get('uncertainties'):
                page_warnings.append(f'{prefix}: unresolved source uncertainties')
            if inferred_count:
                page_warnings.append(f'{prefix}: {inferred_count} rule-inferred style spans; source appearance not confirmed')
        errors.extend(page_errors)
        warnings.extend(page_warnings)
        pages.append({'page': number, 'structure_ok': not page_errors,
                      'needs_review': bool(page_warnings)})
    return {'structure_ok': not errors, 'content_accuracy': 'not_evaluated',
            'style_accuracy': 'not_evaluated', 'image_delivery': 'not_verified_by_this_checker',
            'style_evidence_counts': style_evidence_counts,
            'viewed_images': 'self_report_only', 'pages': pages, 'errors': errors, 'warnings': warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--expected-pages', help='Assert complete requested physical range, e.g. 1-50')
    parser.add_argument('--json-only', action='store_true', help='Validate canonical JSON without a Markdown derivative')
    args = parser.parse_args()
    if args.report.exists():
        parser.error('Report already exists; choose a new path')
    report = validate(args.manifest, args.output_dir,
                      select_pages(args.expected_pages) if args.expected_pages else None,
                      require_markdown=not args.json_only)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'structure_ok': report['structure_ok'], 'errors': len(report['errors']),
                      'warnings': len(report['warnings']), 'content_accuracy': 'not_evaluated'}))
    raise SystemExit(0 if report['structure_ok'] else 1)


if __name__ == '__main__':
    main()
