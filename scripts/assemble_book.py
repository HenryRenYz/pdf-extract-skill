#!/usr/bin/env python3
"""Assemble checked page JSON into traceable book JSONL, without prose exports.

No model calls, text corrections, inferred typography or automatic sentence
merges. Merge candidates are suggestions; applied merges require explicit
source-reviewed decisions and continuous physical/printed page boundaries.
"""
import argparse
import collections
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path

from prepare_images import atomic_json, digest, manifest_errors, select_pages
from validate_outputs import EVIDENCE, STYLES, STYLE_EVIDENCE, TYPES, valid_bbox, validate
from inline_labels import annotate_inline_labels
from document_parts import load_document_parts, part_title_matches
from semantic_contract import SEMANTIC_FIELDS, validate_semantic_block
from source_identity import source_digest, manifest_source_hash
from document_structure import chapter_prefix, chapter_value as chinese_number


def load(path):
    return json.loads(Path(path).read_text())


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def write_jsonl(path, records):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records))
    temporary.replace(path)


def verify_files(root, declared):
    if not isinstance(declared, dict) or not declared:
        raise ValueError('Producer checkpoint has no complete file hash inventory')
    for relative, expected in declared.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or digest(path) != expected:
            raise ValueError('Producer file missing/outside job root/hash changed: ' + relative)
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
    if actual != set(declared) | {'checkpoint.json'}:
        raise ValueError('Producer job file inventory changed after checkpoint')


def read_job(jobs, number, source_hash, page, expected_scope):
    attempts = sorted((jobs / 'pages' / f'{number:04d}').glob('attempt-*'))
    if not attempts:
        raise ValueError(f'No producer attempt for page {number}')
    # Never silently fall back from the latest failed attempt to an older one.
    job = attempts[-1].resolve()
    checkpoint = job / 'checkpoint.json'
    record = load(checkpoint)
    if record.get('status') != 'validated' or record.get('page') != number:
        raise ValueError(f'Latest page {number} checkpoint is not validated')
    contract = load(jobs / 'contract.json')
    if (contract.get('source_pdf_sha256') != source_hash or contract.get('expected_pages') != expected_scope
            or record.get('model') != contract.get('model') or record.get('backend') != contract.get('backend')):
        raise ValueError(f'Page {number} checkpoint source/model/config identity differs from producer contract')
    verify_files(job, record.get('file_hashes'))
    manifest_path = job / 'input/manifest.json'
    local_manifest = load(manifest_path)
    if (manifest_errors(local_manifest, [number])
            or manifest_source_hash(local_manifest) != source_hash
            or local_manifest.get('source_requested_pages') != expected_scope):
        raise ValueError(f'Page {number} job source/scope does not match current source')
    local_page = local_manifest['pages'][0]
    if local_page['original']['sha256'] != page['original']['sha256']:
        raise ValueError(f'Page {number} job original image differs from prepared source')
    if sorted(i['sha256'] for i in local_page['images']) != sorted(i['sha256'] for i in page['images']):
        raise ValueError(f'Page {number} job attachments differ from prepared source')
    checked = validate(manifest_path, job / 'output', [number],
                       require_markdown=not contract.get('json_only', False))
    if not checked['structure_ok']:
        raise ValueError(f'Page {number} job structural revalidation failed: {checked["errors"]}')
    path = job / 'output' / f'{number:04d}.json'
    return load(path), {'page_json_path': str(path), 'page_json_sha256': digest(path),
                        'producer_checkpoint': str(checkpoint), 'producer_checkpoint_sha256': digest(checkpoint),
                        'model': record.get('model'), 'backend': record.get('backend'),
                        'input_kind': 'validated_job'}


def canonical_index(pages_dir, source_hash, expected):
    paths = [pages_dir / 'manifest.json', pages_dir.parent / 'manifest.json']
    index_path = next((p for p in paths if p.is_file()), None)
    if index_path is None:
        raise ValueError('Canonical pages require a sibling/parent manifest.json with final JSON hashes')
    data = load(index_path)
    declared_source = data.get('source_sha256', data.get('source_pdf_sha256', data.get('source', {}).get('pdf_sha256')))
    if declared_source != source_hash:
        raise ValueError('Canonical index source hash differs from current PDF')
    records = data.get('pages')
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError('Canonical index pages must be page/hash records')
    by_page = {r.get('page'): r for r in records}
    if len(by_page) != len(records) or sorted(by_page) != expected:
        raise ValueError('Canonical index scope/duplicate pages differs from --expected-pages')
    return index_path, by_page


def read_canonical(pages_dir, index_path, entry, number, source_hash, page):
    path = pages_dir / f'{number:04d}.json'
    declared_path = entry.get('json_path', entry.get('path'))
    if declared_path:
        candidate = Path(declared_path)
        candidate = candidate.resolve() if candidate.is_absolute() else (index_path.parent / candidate).resolve()
        if candidate != path.resolve():
            raise ValueError(f'Canonical index path differs from allocated page {number}')
    expected = entry.get('sha256', entry.get('json_sha256', entry.get('page_json_sha256')))
    if not expected or not path.is_file() or digest(path) != expected:
        raise ValueError(f'Canonical page {number} missing/hash changed')
    data = load(path)
    if (data.get('source_sha256', data.get('source_pdf_sha256')) != source_hash
            or data.get('source_image_sha256') != page['original']['sha256']):
        raise ValueError(f'Canonical page {number} source/image provenance differs')
    original_path = entry.get('input_json_path', entry.get('input_page_json_path', entry.get('canonical_input_json_path')))
    original_hash = entry.get('input_json_sha256', entry.get('input_page_sha256'))
    if not original_path or not original_hash:
        raise ValueError(f'Canonical page {number} must preserve input JSON path/hash')
    original = Path(original_path)
    original = original.resolve() if original.is_absolute() else (index_path.parent / original).resolve()
    if not original.is_file() or digest(original) != original_hash:
        raise ValueError(f'Canonical page {number} raw input JSON missing/hash changed')
    return data, {'page_json_path': str(path), 'page_json_sha256': digest(path),
                  'input_page_json_path': str(original), 'input_page_json_sha256': original_hash,
                  'canonical_manifest': str(index_path), 'canonical_manifest_sha256': digest(index_path),
                  'normalization': entry.get('normalization', data.get('normalization', {})),
                  'input_kind': 'hash_checked_canonical'}


def check_page(data, number, page, seen_ids, source_hash=None):
    """Validate JSON without requiring an optional Markdown derivative."""
    if not isinstance(data, dict) or type(data.get('page')) is not int or data['page'] != number:
        raise ValueError(f'Page {number} JSON physical page mismatch')
    if 'printed_page' not in data or (data['printed_page'] is not None and type(data['printed_page']) not in (str, int)):
        raise ValueError(f'Page {number} invalid printed_page')
    for key in ('blocks', 'uncertainties', 'changes', 'viewed_images'):
        if not isinstance(data.get(key), list):
            raise ValueError(f'Page {number} {key} is not a list')
    ids = set()
    for block in data['blocks']:
        if not isinstance(block, dict):
            raise ValueError(f'Page {number} invalid block object')
        identifier = block.get('id')
        if not isinstance(identifier, str) or not identifier or identifier in seen_ids or identifier in ids:
            raise ValueError(f'Page {number} duplicate/invalid block id')
        ids.add(identifier)
        if block.get('type') not in TYPES or not isinstance(block.get('text'), str) or block.get('evidence') not in EVIDENCE:
            raise ValueError(f'Block {identifier} invalid type/text/evidence')
        if any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127 for c in block['text']):
            raise ValueError(f'Block {identifier} forbidden control character; inspect escaped LaTeX')
        if 'bbox' not in block or (block['bbox'] is not None and not valid_bbox(block['bbox'], page['width'], page['height'])):
            raise ValueError(f'Block {identifier} invalid source-pixel bbox')
        semantic_errors = validate_semantic_block(block, source_hash or data.get('source_sha256', data.get('source_pdf_sha256')), page['original']['sha256'])
        if semantic_errors:
            raise ValueError(f'Block {identifier} semantic contract: {semantic_errors}')
        if not isinstance(block.get('styles'), list):
            raise ValueError(f'Block {identifier} invalid styles list')
        for style in block['styles']:
            if (not isinstance(style, dict) or style.get('kind') not in STYLES or style.get('evidence') not in STYLE_EVIDENCE
                    or type(style.get('start')) is not int or type(style.get('end')) is not int
                    or not 0 <= style['start'] < style['end'] <= len(block['text'])):
                raise ValueError(f'Block {identifier} invalid Unicode style range/evidence')
            if style['evidence'] == 'rule_inferred' and any(not isinstance(style.get(k), str) or not style[k] for k in ('rule_id', 'rule_version')):
                raise ValueError(f'Block {identifier} inferred style lacks rule id/version')
    if data.get('page_status') == 'blank':
        if data['blocks'] or not isinstance(data.get('blank_reason'), str) or not data['blank_reason'].strip():
            raise ValueError(f'Page {number} invalid blank-page declaration')
    elif data.get('page_status', 'content') != 'content':
        raise ValueError(f'Page {number} unknown page_status')
    elif not ids:
        raise ValueError(f'Page {number} has no source blocks')
    for kind in ('uncertainties', 'changes'):
        for row in data[kind]:
            if (not isinstance(row, dict) or row.get('block_id') not in ids or not isinstance(row.get('reason'), str)
                    or not row['reason'].strip() or 'bbox' not in row
                    or (row['bbox'] is not None and not valid_bbox(row['bbox'], page['width'], page['height']))):
                raise ValueError(f'Page {number} invalid {kind} record')
            if kind == 'changes' and any(not isinstance(row.get(k), str) for k in ('before', 'after')):
                raise ValueError(f'Page {number} change lacks before/after')
    if any(not isinstance(path, str) for path in data['viewed_images']):
        raise ValueError(f'Page {number} invalid self-reported image paths')
    # Job snapshots relocate the same hashed images under NNNN/. Canonical
    # records may retain those paths; recognize only the assigned page mapping.
    accepted = set()
    for image in page['images']:
        aliases = {image['path'], f'{number:04d}/' + Path(image['path']).name}
        if not aliases.intersection(data['viewed_images']):
            raise ValueError(f'Page {number} missing self-reported assigned image: {image["path"]}')
        accepted.update(aliases)
    accepted.update({page['original']['path'], f'{number:04d}/' + Path(page['original']['path']).name})
    if set(data['viewed_images']) - accepted:
        raise ValueError(f'Page {number} self-reported unassigned images')
    seen_ids.update(ids)


def heading_text(text):
    return re.sub(r'\s+', ' ', re.sub(r'^(?:#+\s*)|[*_`]', '', text)).strip()


def chapter_number(text):
    value = heading_text(text)
    if re.search(r'[.．·…]{2,}|\s{2,}\d+\s*$', value):
        return None  # Contents entries with leaders or trailing page numbers.
    return chapter_prefix(value)[0]


def printed_number(value):
    if type(value) is int:
        return ('arabic', value)
    if not isinstance(value, str):
        return None
    token = re.sub(r'^[\s\-—–]+|[\s\-—–]+$', '', value)
    if re.fullmatch(r'\d+', token):
        return ('arabic', int(token))
    if re.fullmatch(r'[IVXLCDMivxlcdm]+', token):
        if not re.fullmatch(r'M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})', token.upper()):
            return None
        tokens = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
        total, previous = 0, 0
        for char in token.upper()[::-1]:
            current = tokens[char]
            total += -current if current < previous else current
            previous = max(previous, current)
        return ('roman', total)
    if re.fullmatch(r'[零〇一二两三四五六七八九十百千]+', token):
        return ('chinese', chinese_number(token))
    return None


def page_kind(blocks, current):
    headings = [heading_text(b['text']) for b in blocks if b['type'] == 'heading']
    if any(re.fullmatch(r'目\s*录|contents|table of contents', text, re.I) for text in headings):
        return 'front-contents'
    if any(re.search(r'前言|序言|preface', text, re.I) for text in headings):
        return 'front-prefaces'
    if any(re.search(r'版权|出版说明|图书在版编目|copyright|publication', text, re.I) for text in headings):
        return 'front-publication'
    if current == 'front-contents':
        leaders = sum(bool(re.search(r'[.．·…]{2,}', b['text'])) for b in blocks)
        entries = sum(bool(re.search(r'\S\s+\d+\s*$', b['text'])) for b in blocks)
        chapters = sum(chapter_number(text) is not None for text in headings)
        if leaders >= 2 or entries >= 3 or chapters >= 2:
            return 'front-contents'
    return None


def source_records(pages, page_meta, provenance, manifest_root, output, source_hash, report, document_parts=None):
    records, current, heading_state, chapter_meta = [], 'unclassified', {}, {}
    for number in sorted(pages):
        data, page = pages[number], page_meta[number]
        part = (document_parts or {}).get(number)
        if part:
            if current != part['part_id']:
                heading_state = {}
            current = part['part_id']
        elif current.startswith('back-') and provenance[number].get('input_kind') == 'reviewed_cover_without_ocr':
            current = f'back-cover-{number}'
            heading_state = {}
        kind = None if part or current.startswith('back-') else page_kind(data['blocks'], current)
        if kind:
            current = kind
        page_heading_state = heading_state
        raw_blocks = data['blocks']
        if data.get('page_status') == 'blank':
            raw_blocks = [{'id': f'p{number:04d}-blank', 'type': 'blank', 'text': '',
                           'bbox': None, 'styles': [], 'evidence': 'unreviewed',
                           'blank_reason': data['blank_reason']}]
        for raw in raw_blocks:
            block = copy.deepcopy(raw)
            title = heading_text(block['text'])
            chapter = chapter_number(title) if block['type'] == 'heading' and current != 'front-contents' and not part else None
            # A genuine single chapter opening after contents may leave the
            # contents state; leader-heavy/multiple-chapter pages may not.
            if block['type'] == 'heading' and current == 'front-contents' and kind is None:
                chapter = chapter_number(title)
            if part:
                block.pop('chapter_number', None)
                block.pop('section_number', None)
                if block['type'] == 'heading':
                    is_title = part_title_matches(title, part)
                    block['heading_depth'] = 1 if is_title else 2
                block['document_part'] = copy.deepcopy(part)
            elif chapter is not None:
                current = f'chapter-{chapter}'
                heading_state = {}
                block['heading_depth'] = 1
                block['chapter_number'] = chapter
            elif block['type'] == 'heading':
                section = re.match(r'^(?:§\s*)?(\d+(?:[.．]\d+)+)(?:\s|[、:：]|$)', title)
                if section and not current.startswith('front-') and current != 'unclassified':
                    block['heading_depth'] = section.group(1).count('.') + section.group(1).count('．') + 1
                    block['section_number'] = section.group(1).replace('．', '.')
                elif section and current in {'front-publication', 'unclassified'}:
                    current = 'chapter-' + section.group(1).split('.')[0]
                    heading_state = {}
                    block['heading_depth'] = section.group(1).count('.') + 1
                    block['section_number'] = section.group(1)
                    report['needs_review'].append({'kind': 'chapter_inferred_from_section', 'block_id': block['id']})
                else:
                    block['heading_depth'] = 1 if current.startswith('front-') or current == 'unclassified' else 2
            hierarchy_evidence = 'structure_inferred'
            explicit = block.get('heading_presentation', {}) if block['type'] == 'heading' else {}
            if 'depth' in explicit and explicit.get('review', {}).get('source_checked') is True:
                # check_page authenticated source/text/image and review evidence.
                # Explicit hierarchy works for articles and other numbering
                # systems without inventing a chapter number from their labels.
                container = explicit.get('container_id')
                if container is not None:
                    if current != container:
                        heading_state = {}
                    current = container
                    block.pop('chapter_number', None)
                    block.pop('section_number', None)
                block['heading_depth'] = explicit['depth']
                hierarchy_evidence = 'source_checked'
            if kind and page_heading_state is heading_state:
                # Switching front matter must not reuse a previous chapter's
                # heading as parent. Reset at the first block once per page.
                prior_chapter = records[-1]['chapter_id'] if records else current
                if prior_chapter != current:
                    heading_state = {}
            if block['type'] == 'heading':
                depth = block['heading_depth']
                block['parent_id'] = heading_state.get(depth-1)
                heading_state = {level: bid for level, bid in heading_state.items() if level < depth}
                heading_state[depth] = block['id']
            else:
                block['parent_id'] = heading_state.get(max(heading_state, default=0))
            original = (manifest_root / page['original']['path']).resolve()
            block.update(block_id=block['id'], page=number, printed_page=data.get('printed_page'),
                         source_sha256=source_hash, source_image=os.path.relpath(original, output),
                         source_image_absolute_path=str(original), source_image_sha256=page['original']['sha256'],
                         chapter_id=current, hierarchy_evidence=hierarchy_evidence,
                         text_sha256=text_hash(block['text']), provenance=provenance[number],
                         uncertainties=[copy.deepcopy(row) for row in data['uncertainties'] if row['block_id'] == block['id']],
                         changes=[copy.deepcopy(row) for row in data['changes'] if row['block_id'] == block['id']])
            if block['evidence'] == 'source_checked' and provenance[number].get('input_kind') != 'reviewed_cover_without_ocr':
                block['model_evidence'] = 'source_checked'
                block['evidence'] = 'unreviewed'
                report['evidence_downgrades'].append({'block_id': block['id'], 'field': 'evidence'})
            for index, style in enumerate(block['styles']):
                if style['evidence'] == 'source_checked':
                    style['model_evidence'] = 'source_checked'
                    style['evidence'] = 'unreviewed'
                    report['evidence_downgrades'].append({'block_id': block['id'], 'field': f'styles[{index}].evidence'})
            metadata = chapter_meta.setdefault(current, {'chapter_id': current, 'pages': [], 'heading_block_ids': []})
            if number not in metadata['pages']:
                metadata['pages'].append(number)
            if block['type'] == 'heading':
                metadata['heading_block_ids'].append(block['id'])
                if 'title' not in metadata or block.get('heading_depth') == 1:
                    metadata.setdefault('title', title)
            records.append(block)
    report['chapters'] = list(chapter_meta.values())
    return records


def source_location(block):
    return {key: block[key] for key in ('block_id', 'page', 'printed_page', 'bbox', 'source_sha256',
                                        'source_image', 'source_image_absolute_path', 'source_image_sha256', 'text_sha256')}


def reading_records(source, report=None):
    records = []
    for block in source:
        if block['type'] in {'header', 'footer'}:
            continue
        record = copy.deepcopy(block)
        record.update(source_block_ids=[block['block_id']], text_source_block_ids=[block['block_id']],
                      sources=[source_location(block)], pages=[block['page']],
                      source_styles=[{'block_id': block['block_id'], 'text_sha256': block['text_sha256'],
                                      'styles': copy.deepcopy(block['styles'])}],
                      source_semantics=[{'block_id': block['block_id'], 'text_sha256': block['text_sha256'],
                                         'fields': {key: copy.deepcopy(block[key]) for key in SEMANTIC_FIELDS if key in block}}])
        records.append(record)
    if report is not None:
        paragraph_boundary_inventory(records, report)
    return records


def paragraph_boundary_inventory(records, report):
    """Keep display/text and declared paragraph relationships visible to review."""
    report['paragraph_boundaries'] = []
    text_types = {'paragraph', 'formula', 'theorem', 'proof', 'example', 'exercise', 'list', 'grammar', 'display_group'}
    for left, right in zip(records, records[1:]):
        presentation = right.get('paragraph_presentation')
        display_text = left['type'] in text_types and right['type'] in text_types and 'formula' in {left['type'], right['type']}
        if presentation is None and not display_text:
            continue
        presentation = presentation or {}
        boundary = presentation.get('boundary_before', 'unknown')
        reviewed = presentation.get('review', {}).get('source_checked') is True and boundary != 'unknown'
        report['paragraph_boundaries'].append({'left_block_id': left['block_id'], 'right_block_id': right['block_id'],
            'boundary_before': boundary, 'first_line': presentation.get('first_line', 'inherit'), 'source_checked': reviewed,
            'pages': [left['page'], right['page']]})
        if not reviewed:
            report.setdefault('needs_review', []).append({'kind': 'unresolved_paragraph_boundary',
                'left_block_id': left['block_id'], 'right_block_id': right['block_id']})


def bind_table_captions(records, report):
    """Associate classified adjacent source captions, never invent a title."""
    report['table_caption_bindings'] = []
    for index, block in enumerate(records):
        if block['type'] != 'table' or index == 0 or block.get('caption_block_id'):
            continue
        caption = records[index-1]
        title = caption['text'].lstrip(' \t\n*')
        if (caption['type'] != 'caption' or caption['page'] != block['page']
                or caption['chapter_id'] != block['chapter_id']
                or caption.get('caption_for_figure_ids') or not re.match(r'表\s*\d', title)):
            continue
        block['caption_block_id'] = caption['block_id']
        block['caption_binding_evidence'] = 'adjacent_classified_source_table_caption'
        caption.setdefault('caption_for_table_ids', []).append(block['block_id'])
        report['table_caption_bindings'].append({'table_block_id': block['block_id'],
                                                'caption_block_id': caption['block_id'], 'page': block['page']})


def footnote_marker_span(text, marker):
    if not isinstance(marker, str) or not marker or len(marker) > 8 or any(c.isspace() for c in marker):
        raise ValueError('Footnote marker must be a nonempty explicit source marker')
    escaped = re.escape(marker)
    matches = list(re.finditer(r'<sup>\s*'+escaped+r'\s*</sup>|\\textsuperscript\{'+escaped+r'\}', text, re.I))
    if not matches and len(marker) == 1 and (marker in '¹²³⁴⁵⁶⁷⁸⁹⁰*†‡' or '\u2460' <= marker <= '\u2473'):
        matches = list(re.finditer(escaped, text))
    if len(matches) != 1:
        raise ValueError('Footnote marker must occur exactly once as a recognized anchor superscript/source symbol')
    match = matches[0]
    return match.start(), match.end(), match.group()


def apply_footnotes(records, decision_path, source_hash, source, report):
    """Move explicitly reviewed note records under their unchanged text anchor."""
    if not decision_path:
        return records
    decisions = load(decision_path)
    if isinstance(decisions, dict):
        if decisions.get('source_sha256') != source_hash:
            raise ValueError('Footnote decision document source hash differs')
        decisions = decisions.get('decisions')
    if not isinstance(decisions, list):
        raise ValueError('Footnote decisions must be a list or source_sha256/decisions object')
    by_id = {b['block_id']: b for b in records}
    source_order = collections.defaultdict(list)
    for block in source:
        source_order[block['page']].append(block['block_id'])
    anchors, claimed, anchor_markers, prepared = set(), set(), set(), []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError('Footnote decision must be an object')
        aid, note_ids, marker = decision.get('anchor_block_id'), decision.get('block_ids'), decision.get('marker')
        if aid not in by_id or not isinstance(note_ids, list) or not note_ids or any(not isinstance(n, str) or n not in by_id for n in note_ids):
            raise ValueError('Footnote decision has unknown anchor/note body ids')
        if len(set(note_ids)) != len(note_ids) or claimed.intersection(note_ids) or aid in note_ids or (aid, marker) in anchor_markers:
            raise ValueError('Footnote decisions duplicate a source claim/anchor marker')
        if (decision.get('source_sha256') != source_hash or decision.get('source_checked') is not True
                or not isinstance(decision.get('reason'), str) or not decision['reason'].strip()):
            raise ValueError('Footnote ownership requires matching source, actual source_checked and review reason')
        anchor = by_id[aid]
        if anchor['type'] in {'figure', 'table', 'caption'}:
            raise ValueError('Footnote anchor must be a text block with an explicit marker')
        if any(by_id[n]['page'] != anchor['page'] for n in note_ids):
            raise ValueError('Footnote body and anchor must be on the same physical source page')
        order = source_order[anchor['page']]
        positions = [order.index(n) for n in note_ids]
        if positions != list(range(positions[0], positions[0]+len(positions))):
            raise ValueError('Footnote ids must be ordered and contiguous in the original source block sequence')
        start, end, token = footnote_marker_span(anchor['text'], marker)
        first = by_id[note_ids[0]]['text'].lstrip()
        leading = re.match(r'(?:<sup>\s*'+re.escape(marker)+r'\s*</sup>|'+re.escape(marker)+r')(?=\s|[.．、:：]|$)', first, re.I)
        if not leading:
            raise ValueError('Footnote body first block does not begin with the reviewed source marker')
        evidence_name = decision.get('evidence_image')
        if not isinstance(evidence_name, str) or not evidence_name:
            raise ValueError('Footnote review must retain an actual evidence_image')
        evidence_path = Path(evidence_name).expanduser()
        evidence_path = evidence_path.resolve() if evidence_path.is_absolute() else (decision_path.parent/evidence_path).resolve()
        if not evidence_path.is_file():
            raise ValueError('Footnote review evidence image is absent')
        evidence_hash = digest(evidence_path)
        if decision.get('evidence_image_sha256', evidence_hash) != evidence_hash:
            raise ValueError('Footnote evidence image hash changed')
        reviewed = {**copy.deepcopy(decision), 'evidence_image': str(evidence_path), 'evidence_image_sha256': evidence_hash}
        prepared.append((anchor, note_ids, {'marker': marker, 'anchor_block_id': aid,
                         'marker_start': start, 'marker_end': end, 'marker_source_text': token,
                         'blocks': [copy.deepcopy(by_id[n]) for n in note_ids], 'review': reviewed}))
        claimed.update(note_ids)
        anchors.add(aid)
        anchor_markers.add((aid, marker))
    if anchors.intersection(claimed):
        raise ValueError('Footnote anchors cannot themselves be claimed as another note body')
    for anchor, note_ids, note in prepared:
        anchor.setdefault('footnotes', []).append(note)
        for identifier in note_ids:
            block = by_id[identifier]
            anchor['source_block_ids'] += block['source_block_ids']
            anchor['sources'] += block['sources']
            anchor['source_styles'] += block['source_styles']
        report['applied_footnotes'].append({'anchor_block_id': anchor['block_id'], 'marker': note['marker'],
                                           'source_block_ids': note_ids, 'review': note['review']})
    for anchor_id in anchors:
        by_id[anchor_id]['footnotes'].sort(key=lambda row: row['marker_start'])
    return [block for block in records if block['block_id'] not in claimed]


def iou(left, right):
    if not left or not right:
        return 0
    intersection = max(0, min(left[2], right[2])-max(left[0], right[0])) * max(0, min(left[3], right[3])-max(left[1], right[1]))
    area_left = (left[2]-left[0])*(left[3]-left[1])
    area_right = (right[2]-right[0])*(right[3]-right[1])
    return intersection / (area_left+area_right-intersection) if area_left+area_right > intersection else 0


def bind_figures(records, registry_path, source_hash, page_meta, report):
    registry = load(registry_path) if registry_path else []
    figures = registry if isinstance(registry, list) else registry.get('figures') if isinstance(registry, dict) else None
    if not isinstance(figures, list):
        raise ValueError('Figure registry must be a list or object with figures list')
    registry_hash = digest(registry_path) if registry_path else None
    by_id = {b['block_id']: b for b in records}
    seen, bound, collisions = set(), set(), set()
    for figure in figures:
        if not isinstance(figure, dict):
            raise ValueError('Figure registry item must be an object')
        identifier = figure.get('figure_id', figure.get('id'))
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError('Figure registry duplicate/missing id')
        seen.add(identifier)
        origin = figure.get('source', {})
        number = figure.get('physical_page', figure.get('page', origin.get('page')))
        box = figure.get('bbox_px', origin.get('bbox_px'))
        declared_hash = figure.get('source_sha256', origin.get('pdf_sha256'))
        if declared_hash is None and isinstance(registry, dict):
            declared_hash = registry.get('source_sha256', registry.get('source_pdf_sha256'))
        if declared_hash != source_hash or number not in page_meta:
            raise ValueError(f'Figure {identifier} source hash/page differs from input')
        if not valid_bbox(box, page_meta[number]['width'], page_meta[number]['height']):
            raise ValueError(f'Figure {identifier} invalid original source-pixel bbox')
        explicit = figure.get('block_id', figure.get('source_block_id'))
        if explicit is None and isinstance(figure.get('block_ids'), list) and len(figure['block_ids']) == 1:
            explicit = figure['block_ids'][0]
        caption_id = figure.get('caption_block_id')
        if caption_id and (caption_id not in by_id or by_id[caption_id]['page'] != number):
            raise ValueError(f'Figure {identifier} assigned caption block does not exist on source page')
        label = figure.get('source_caption', figure.get('printed_number'))
        if not caption_id and isinstance(label, str) and label.strip():
            captions = [b for b in records if b['page'] == number and b['type'] == 'caption'
                        and re.sub(r'\s+', '', b['text']) == re.sub(r'\s+', '', label)]
            if len(captions) == 1:
                caption_id = captions[0]['block_id']
            elif len(captions) > 1:
                report['needs_review'].append({'kind': 'ambiguous_figure_caption', 'figure_id': identifier,
                                               'block_ids': [b['block_id'] for b in captions]})
        if explicit:
            if explicit not in by_id or by_id[explicit]['page'] != number:
                raise ValueError(f'Figure {identifier} explicit block binding differs from source page')
            candidates = [by_id[explicit]]
        else:
            candidates = [b for b in records if b['page'] == number and b['type'] == 'figure' and iou(b.get('bbox'), box) >= .10]
            named = [b for b in candidates if b.get('figure_id') == identifier
                     or caption_id and b.get('caption_block_id') == caption_id
                     or isinstance(label, str) and re.sub(r'\s+', '', b['text']) == re.sub(r'\s+', '', label)]
            if named:
                candidates = named
        if len(candidates) != 1:
            report['needs_review'].append({'kind': 'unbound_registry_figure' if not candidates else 'ambiguous_figure_binding',
                                           'figure_id': identifier, 'page': number,
                                           'candidates': [{'block_id': b['block_id'], 'iou': iou(b.get('bbox'), box)} for b in candidates]})
            continue
        target = candidates[0]
        if target['block_id'] in bound or target['block_id'] in collisions:
            report['needs_review'].append({'kind': 'multiple_figures_target_same_block', 'figure_id': identifier,
                                           'block_id': target['block_id']})
            collisions.add(target['block_id'])
            bound.discard(target['block_id'])
            report['figure_bindings'] = [row for row in report['figure_bindings'] if row['block_id'] != target['block_id']]
            target['figure_binding_status'] = 'ambiguous_multiple_registry_entries'
            for key in ('figure_id', 'figure', 'figure_registry', 'figure_registry_sha256', 'figure_binding_evidence', 'figure_bbox_iou'):
                target.pop(key, None)
            continue
        bound.add(target['block_id'])
        target.update(figure_id=identifier, figure=copy.deepcopy(figure), figure_registry=str(registry_path),
                      figure_registry_sha256=registry_hash, figure_binding_evidence='explicit_registry_block' if explicit else 'unique_source_bbox_and_caption',
                      figure_bbox_iou=iou(target.get('bbox'), box))
        if caption_id:
            target['caption_block_id'] = caption_id
            by_id[caption_id].setdefault('caption_for_figure_ids', []).append(identifier)
        report['figure_bindings'].append({'figure_id': identifier, 'block_id': target['block_id'], 'page': number,
                                          'caption_block_id': caption_id, 'iou': target['figure_bbox_iou']})
    for block in records:
        if block['type'] == 'figure' and block['block_id'] not in bound and not block.get('cover'):
            report['needs_review'].append({'kind': 'unbound_source_figure_block', 'block_id': block['block_id'], 'page': block['page']})


def intervening_blocks(left, right, page_bodies):
    before = page_bodies[left['page']]
    after = page_bodies[right['page']]
    left_index = next(i for i, row in enumerate(before) if row['block_id'] == left['block_id'])
    right_index = next(i for i, row in enumerate(after) if row['block_id'] == right['block_id'])
    return before[left_index+1:]+after[:right_index]


def boundary_errors(left, right, page_bodies, allow_intervening_figures=False, allow_unknown_printed_page=False):
    errors = []
    if right['page'] != left['page']+1:
        errors.append('Physical pages are not adjacent')
    if left['chapter_id'] != right['chapter_id']:
        errors.append('Chapter boundary cannot be merged')
    first, second = printed_number(left['printed_page']), printed_number(right['printed_page'])
    if first is None or second is None:
        unknown_only = all(parsed is not None or value is None or value == ''
                           for parsed, value in ((first, left['printed_page']), (second, right['printed_page'])))
        if not allow_unknown_printed_page or not unknown_only:
            errors.append('Printed page number is unknown/unparseable')
    elif first[0] != second[0] or second[1] != first[1]+1:
        errors.append('Printed page discontinuity')
    intervening = intervening_blocks(left, right, page_bodies)
    if intervening:
        if not allow_intervening_figures:
            errors.append('Pair is not the actual last/first body block boundary')
        elif any(row['type'] not in {'figure', 'caption'} for row in intervening):
            errors.append('Reviewed intervening-figure exception cannot skip paragraph/formula/heading/table/other body blocks')
    return errors


def merge_candidates(records, report):
    """Inventory every adjacent physical-page body boundary without text guesses."""
    pages = collections.defaultdict(list)
    for block in records:
        pages[block['page']].append(block)
    for number in sorted(pages):
        if number+1 not in pages:
            continue
        pairs = [(pages[number][-1], pages[number+1][0], False)]
        # Also offer a separately identified review across retained illustrations.
        left_text = [b for b in pages[number] if b['type'] not in {'figure', 'caption'}]
        right_text = [b for b in pages[number+1] if b['type'] not in {'figure', 'caption'}]
        if left_text and right_text and (left_text[-1], right_text[0]) != pairs[0][:2]:
            pairs.append((left_text[-1], right_text[0], True))
        for left, right, allow_intervening in pairs:
            intervening = intervening_blocks(left, right, pages)
            errors = boundary_errors(left, right, pages, allow_intervening)
            report.setdefault('merge_candidates', []).append({
                'left_block_id': left['block_id'], 'right_block_id': right['block_id'],
                'left_text_sha256': left['text_sha256'], 'right_text_sha256': right['text_sha256'],
                'left_source_image_sha256': left['source_image_sha256'],
                'right_source_image_sha256': right['source_image_sha256'],
                'source_sha256': left['source_sha256'], 'pages': [number, number+1],
                'eligible_after_source_review': not errors, 'blocked_reasons': errors,
                'requires_allow_intervening_figures': bool(intervening),
                'intervening_source_block_ids': [b['block_id'] for b in intervening],
                'automatic_action': 'none; requires explicit reviewed decision'})
    return pages


def _check_boundary_decision(decision, left, right, source_hash):
    if (decision.get('source_sha256') != source_hash or decision.get('source_checked') is not True
            or not isinstance(decision.get('reason'), str) or not decision['reason'].strip()):
        raise ValueError('Boundary decision requires matching source, actual source_checked and reason')
    # Old decision files remain admissible; any supplied identity is binding.
    for name, expected in (('left_text_sha256', left['text_sha256']),
                           ('right_text_sha256', right['text_sha256']),
                           ('left_source_image_sha256', left['source_image_sha256']),
                           ('right_source_image_sha256', right['source_image_sha256'])):
        if name in decision and decision[name] != expected:
            raise ValueError('Boundary decision identity changed: ' + name)
    if decision.get('boundary_before', 'continue' if decision.get('action', 'merge') != 'keep' else 'new_paragraph') not in {'continue', 'new_paragraph'}:
        raise ValueError('Reviewed boundary cannot remain unknown')


def apply_decisions(records, decision_path, source_hash, report):
    page_bodies = merge_candidates(records, report)
    decisions = load(decision_path) if decision_path else []
    if isinstance(decisions, dict):
        if decisions.get('source_sha256') != source_hash:
            raise ValueError('Merge decision document source hash differs')
        decisions = decisions.get('decisions')
    if not isinstance(decisions, list):
        raise ValueError('Merge decisions must be list or source_sha256/decisions object')
    originals = {b['block_id']: b for b in records}
    order = {b['block_id']: index for index, b in enumerate(records)}
    owners = {b['block_id']: b for b in records}
    removed, pairs = set(), set()
    for decision in sorted(decisions, key=lambda d: order.get(d.get('left_block_id'), -1) if isinstance(d, dict) else -1):
        if not isinstance(decision, dict):
            raise ValueError('Merge decision must be an object')
        a, b = decision.get('left_block_id'), decision.get('right_block_id')
        if a not in originals or b not in originals or (a, b) in pairs:
            raise ValueError('Merge decision contains unknown/duplicate block pair')
        pairs.add((a, b))
        action = decision.get('action', 'merge')
        left, right = originals[a], originals[b]
        _check_boundary_decision(decision, left, right, source_hash)
        if action == 'keep':
            if decision.get('boundary_before', 'new_paragraph') != 'new_paragraph':
                raise ValueError('keep requires a reviewed new_paragraph boundary')
            report['reviewed_boundaries'].append(copy.deepcopy(decision))
            if right['page'] != left['page']+1 or intervening_blocks(left, right, page_bodies):
                raise ValueError('keep must identify the actual adjacent-page body boundary')
            owners[b].setdefault('boundary_decisions', []).append(copy.deepcopy(decision))
            continue
        if decision.get('boundary_before', 'continue') != 'continue':
            raise ValueError('merge/link require a reviewed continuation boundary')
        allow_intervening = decision.get('allow_intervening_figures') is True
        errors = boundary_errors(left, right, page_bodies, allow_intervening,
                                 decision.get('allow_unknown_printed_page') is True)
        if errors:
            raise ValueError(f'Unsafe boundary {a}/{b}: {errors}')
        if action == 'link':
            owners[a].setdefault('cross_page_links', []).append({'target_block_id': b, 'relation': 'reviewed_continuation',
                                                               'decision': copy.deepcopy(decision)})
            report['reviewed_boundaries'].append(copy.deepcopy(decision))
            continue
        if action != 'merge':
            raise ValueError('Boundary action must be merge/link/keep')
        ordinary = left['type'] in {'paragraph', 'formula'} and right['type'] in {'paragraph', 'formula'}
        semantic_continuation = left['type'] in {'theorem', 'proof', 'example', 'exercise', 'list'} and right['type'] == 'paragraph'
        structured_continuation = left['type'] == right['type'] and left['type'] in {'list'}
        if not ordinary and not semantic_continuation and not structured_continuation:
            raise ValueError('Figure/table/heading boundaries require link or keep; never concatenate their text')
        if (left['type'] != 'paragraph' or right['type'] != 'paragraph') and decision.get('allow_nonparagraph') is not True:
            raise ValueError('Nonparagraph continuation merge requires explicit allow_nonparagraph')
        separator = decision.get('separator', '')
        if not isinstance(separator, str) or separator.strip() or len(separator) > 2 or any(c not in ' \n\t' for c in separator):
            raise ValueError('Merge separator may only add at most two whitespace characters')
        x, y = owners[a], owners[b]
        if x is y or x['text_source_block_ids'][-1] != a or y['text_source_block_ids'][0] != b:
            raise ValueError('Merge decision would reorder/interleave source blocks')
        if x.get('figure_id') or y.get('figure_id'):
            raise ValueError('Asset-bound blocks must stay separate; use a reviewed link')
        if x.get('items') and right['type'] == 'paragraph':
            path = decision.get('list_item_path')
            if not isinstance(path, list) or not path or any(type(i) is not int or i < 0 for i in path):
                raise ValueError('Structured list prose continuation requires explicit list_item_path')
            target_items = x['items']
            for depth, item_index in enumerate(path):
                if not isinstance(target_items, list) or item_index >= len(target_items):
                    raise ValueError('Reviewed list_item_path target is absent')
                item = target_items[item_index]
                if depth < len(path)-1:
                    if not isinstance(item, dict):
                        raise ValueError('Reviewed list_item_path cannot descend through a string item')
                    target_items = item.get('children')
            if isinstance(item, str):
                if y['styles']:
                    target_items[path[-1]] = {'text': item+separator+y['text'], 'styles': [
                        {**copy.deepcopy(style), 'start': style['start']+len(item)+len(separator),
                         'end': style['end']+len(item)+len(separator)} for style in y['styles']]}
                else:
                    target_items[path[-1]] = item+separator+y['text']
            else:
                item_offset = len(item['text'])+len(separator)
                item['text'] += separator+y['text']
                item.setdefault('styles', []).extend({**copy.deepcopy(style), 'start': style['start']+item_offset,
                    'end': style['end']+item_offset} for style in y['styles'])
        offset = len(x['text'])+len(separator)
        x['text'] += separator+y['text']
        x['styles'] += [{**copy.deepcopy(style), 'start': style['start']+offset, 'end': style['end']+offset} for style in y['styles']]
        x['source_block_ids'] += y['source_block_ids']
        x['text_source_block_ids'] += y['text_source_block_ids']
        x['sources'] += y['sources']
        x['source_styles'] += y['source_styles']
        x.setdefault('source_semantics', []).extend(copy.deepcopy(y.get('source_semantics', [])))
        if y.get('items'):
            if left['type'] != 'list':
                raise ValueError('Structured list items cannot be consumed by a non-list')
            x.setdefault('items', []).extend(copy.deepcopy(y['items']))
        # Presentation identities describe each immutable source fragment. Merged
        # readers must use the retained source_semantics, never relabel stale hashes.
        for key in ('paragraph_presentation', 'list_presentation', 'code_presentation'):
            if key in x:
                x.setdefault('merged_presentations', {})[key] = copy.deepcopy(x.pop(key))
        x['pages'] = sorted(set(x['pages']+y['pages']))
        x['bbox'] = None
        x['text_sha256'] = text_hash(x['text'])
        if y.get('footnotes'):
            shifted_notes = copy.deepcopy(y['footnotes'])
            for note in shifted_notes:
                note['marker_start'] += offset
                note['marker_end'] += offset
            x.setdefault('footnotes', []).extend(shifted_notes)
        x['evidence'] = 'uncertain' if 'uncertain' in (x['evidence'], y['evidence']) else 'unreviewed'
        x['merge_evidence'] = 'explicit_source_reviewed_boundary; source transcription evidence remains separate'
        skipped = intervening_blocks(left, right, page_bodies)
        if skipped:
            x.setdefault('merge_intervening_source_block_ids', []).extend(row['block_id'] for row in skipped)
            x['reading_order_change'] = 'reviewed continuation text precedes retained intervening figure/caption blocks; original order remains in source-blocks'
        x.setdefault('merge_decisions', []).append(copy.deepcopy(decision))
        for original_id in y['source_block_ids']:
            owners[original_id] = x
        removed.add(y['block_id'])
        report['applied_merges'].append({'block_id': x['block_id'], 'left_block_id': a, 'right_block_id': b,
                                         'source_block_ids': list(x['source_block_ids']),
                                         'intervening_source_block_ids': [row['block_id'] for row in skipped],
                                         'decision': copy.deepcopy(decision)})
    return [b for b in records if b['block_id'] not in removed]


def display_group_members(group, reading_by_id, owner_id):
    """Resolve source-ordered column ownership without inventing captions."""
    errors = []
    members = list(group.get('block_ids', [])) + list(group.get('caption_block_ids', []))
    for member in group.get('caption_block_ids', []):
        if member not in reading_by_id or reading_by_id[member]['type'] != 'caption':
            errors.append('Display group caption target/type missing: ' + str(member))
    columns = group.get('columns', [])
    if 'columns' in group and (not isinstance(columns, list) or not 2 <= len(columns) <= 4):
        return members, errors + ['Display group requires 2-4 columns: ' + owner_id]
    for column in columns:
        if not isinstance(column, dict) or ('code_id' in column) == ('block_ids' in column):
            errors.append('Display group column requires exactly one of code_id or block_ids: ' + owner_id)
            continue
        ids = [column['code_id']] if 'code_id' in column else column['block_ids']
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids):
            errors.append('Display group column requires explicit nonempty body IDs: ' + owner_id)
            continue
        for member in ids:
            members.append(member)
            allowed = {'code'} if 'code_id' in column else {'paragraph', 'list', 'code', 'grammar'}
            if member not in reading_by_id or reading_by_id[member]['type'] not in allowed:
                errors.append('Display group column target/type missing: ' + str(member))
        if 'caption_id' in column:
            member = column['caption_id']
            if not isinstance(member, str) or not member:
                errors.append('Display group caption requires an explicit ID: ' + owner_id)
            else:
                members.append(member)
                if member not in reading_by_id or reading_by_id[member]['type'] != 'caption':
                    errors.append('Display group column target/type missing: ' + str(member))
    if any(member not in reading_by_id or member == owner_id for member in members):
        errors.append('Display group source member missing/self-reference: ' + owner_id)
    if len(members) != len(set(members)):
        errors.append('Display group member consumed more than once: ' + owner_id)
    order = group.get('reading_order')
    if order is not None and (not isinstance(order, list) or any(not isinstance(x, str) for x in order)
                              or collections.Counter(order) != collections.Counter(members)):
        errors.append('Display group reading order differs from membership: ' + owner_id)
    return members, errors


def validate_assembly(source, reading):
    errors = []
    expected = [b['block_id'] for b in source if b['type'] not in {'header', 'footer'}]
    referenced = [s['block_id'] for b in reading for s in b['sources']]
    if collections.Counter(expected) != collections.Counter(referenced):
        errors.append('Every source body block must map to a reading block exactly once')
    originals = {b['block_id']: b for b in source}
    images = {b['source_image_absolute_path']: b['source_image_sha256'] for b in source}
    for filename, expected_hash in images.items():
        path = Path(filename)
        if not path.is_file() or digest(path) != expected_hash:
            errors.append('Original source image changed during assembly: ' + filename)
    source_ids = set(originals)
    reading_by_id = {b['block_id']: b for b in reading}
    grouped_ids = set()
    for block in reading:
        if block['type'] != 'display_group':
            continue
        for group in block.get('groups', []):
            if not isinstance(group, dict):
                errors.append('Invalid display group object: ' + block['block_id'])
                continue
            members, group_errors = display_group_members(group, reading_by_id, block['block_id'])
            errors.extend(group_errors)
            if grouped_ids.intersection(members):
                errors.append('Display group member consumed more than once: ' + block['block_id'])
            grouped_ids.update(members)
    for block in reading:
        if block['text_sha256'] != text_hash(block['text']):
            errors.append('Reading text hash differs: ' + block['block_id'])
        if block['parent_id'] is not None and block['parent_id'] not in source_ids:
            errors.append('Parent id is not an original source block: ' + block['block_id'])
        for style in block['styles']:
            if not 0 <= style['start'] < style['end'] <= len(block['text']):
                errors.append('Merged Unicode style offset invalid: ' + block['block_id'])
        for location in block['sources']:
            original = originals.get(location['block_id'])
            if original is None or location != source_location(original):
                errors.append('Source location changed/lost: ' + block['block_id'])
        if block['source_block_ids'] != [s['block_id'] for s in block['sources']]:
            errors.append('Source id list differs from source locations: ' + block['block_id'])
        expected_styles = [{'block_id': identifier, 'text_sha256': originals[identifier]['text_sha256'],
                            'styles': originals[identifier]['styles']}
                           for identifier in block['source_block_ids'] if identifier in originals]
        if block.get('source_styles') != expected_styles:
            errors.append('Source style fields changed/lost: ' + block['block_id'])
        semantics = block.get('source_semantics', [])
        if [row.get('block_id') for row in semantics] != block.get('text_source_block_ids', block['source_block_ids']):
            errors.append('Source semantics identity/order lost: ' + block['block_id'])
        for row in semantics:
            original = originals.get(row.get('block_id'))
            if original is None or row.get('text_sha256') != original['text_sha256'] or row.get('fields') != {key: original[key] for key in SEMANTIC_FIELDS if key in original}:
                errors.append('Source semantic fields changed/lost: ' + block['block_id'])
        if len(block.get('text_source_block_ids', block['source_block_ids'])) == 1:
            original = originals.get(block['text_source_block_ids'][0])
            if original and any(block.get(key) != original.get(key) for key in SEMANTIC_FIELDS if key in original and key not in {'caption_block_id'}):
                errors.append('Reading semantic fields changed/lost: ' + block['block_id'])
        text_ids = block.get('text_source_block_ids', block['source_block_ids'])
        if len(text_ids) > 1 and text_ids[0] in originals:
            first = originals[text_ids[0]]
            expected_presentations = {key: first[key] for key in
                ('paragraph_presentation', 'list_presentation', 'code_presentation') if key in first}
            if block.get('merged_presentations', {}) != expected_presentations:
                errors.append('Merged presentation differs from first source fragment: ' + block['block_id'])
        # Reconstruct from immutable fragments and explicit separators. A new
        # text hash alone cannot prove that assembly preserved source content.
        separators = {(row.get('left_block_id'), row.get('right_block_id')): row.get('separator', '')
                      for row in block.get('merge_decisions', [])}
        expected_text, expected_spans = '', []
        for index, identifier in enumerate(text_ids):
            original = originals.get(identifier)
            if original is None:
                continue
            if index:
                pair = (text_ids[index-1], identifier)
                if pair not in separators:
                    errors.append('Merged text lacks explicit source boundary: ' + block['block_id'])
                expected_text += separators.get(pair, '')
            offset = len(expected_text)
            expected_spans.extend({**style, 'start': style['start']+offset, 'end': style['end']+offset}
                                  for style in original['styles'])
            expected_text += original['text']
        if block['text'] != expected_text or block['styles'] != expected_spans:
            errors.append('Reading text/styles differ from source fragments: ' + block['block_id'])
        nested_ids = []
        for note in block.get('footnotes', []):
            start, end = note.get('marker_start'), note.get('marker_end')
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(block['text'])
                    or block['text'][start:end] != note.get('marker_source_text') or note.get('anchor_block_id') not in text_ids):
                errors.append('Footnote anchor marker/source offset invalid: ' + block['block_id'])
            for child in note.get('blocks', []):
                original = originals.get(child['block_id'])
                if original is None or child['text'] != original['text'] or child['type'] != original['type'] or child['styles'] != original['styles']:
                    errors.append('Footnote source body text/type/styles changed or lost: ' + block['block_id'])
                if original and any(child.get(key) != original.get(key) for key in SEMANTIC_FIELDS if key in original):
                    errors.append('Footnote semantic fields changed/lost: ' + block['block_id'])
                if original and child.get('source_semantics') != [{'block_id': original['block_id'], 'text_sha256': original['text_sha256'],
                      'fields': {key: original[key] for key in SEMANTIC_FIELDS if key in original}}]:
                    errors.append('Footnote source semantics changed/lost: ' + block['block_id'])
                for location in child['sources']:
                    if location != source_location(originals[location['block_id']]):
                        errors.append('Footnote nested source location changed: ' + block['block_id'])
                    nested_ids.append(location['block_id'])
        if collections.Counter(text_ids+nested_ids) != collections.Counter(block['source_block_ids']):
            errors.append('Text and nested footnote source partition differs: ' + block['block_id'])
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--pages-dir', type=Path, help='Hash-indexed canonical JSON pages')
    inputs.add_argument('--jobs-dir', type=Path, help='run_page_jobs root, validated latest attempts only')
    parser.add_argument('--expected-pages', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', '--source-pdf', dest='source_pdf', required=True, type=Path)
    parser.add_argument('--figures', type=Path)
    parser.add_argument('--merge-decisions', type=Path)
    parser.add_argument('--footnote-decisions', type=Path, help='Explicit source-reviewed anchor/marker/contiguous note-body assignments')
    parser.add_argument('--label-rules', type=Path, help='Source-reviewed structural label spacing rule')
    parser.add_argument('--table-layouts', type=Path, help='Source-reviewed table ruling patches')
    parser.add_argument('--covers', type=Path, help='Reviewed native covers; these pages do not require OCR jobs')
    parser.add_argument('--document-parts', type=Path, help='Explicit source-reviewed back matter roles/columns')
    args = parser.parse_args()
    for key in ('manifest', 'pages_dir', 'jobs_dir', 'output', 'source_pdf', 'figures', 'merge_decisions', 'footnote_decisions', 'label_rules', 'table_layouts', 'covers', 'document_parts'):
        if getattr(args, key):
            setattr(args, key, getattr(args, key).expanduser().resolve())
    expected, started = select_pages(args.expected_pages), time.perf_counter()
    args.output.mkdir(parents=True, exist_ok=True)
    targets = [args.output/'source-blocks.jsonl', args.output/'blocks.jsonl', args.output/'assembly-report.json']
    if any(path.exists() for path in targets):
        parser.error('Assembly output exists; choose a fresh output directory to preserve previous provenance')
    report = {'schema_version': 1, 'status': 'assembling', 'expected_pages': expected, 'errors': [], 'needs_review': [],
              'evidence_downgrades': [], 'merge_candidates': [], 'applied_merges': [], 'reviewed_boundaries': [],
              'figure_bindings': [], 'content_accuracy': 'not_evaluated', 'style_accuracy': 'not_evaluated',
              'applied_footnotes': [],
              'source_image_delivery': 'CLI/self-report evidence only; no server-side pixel audit',
              'started_unix': time.time(), 'assembler_sha256': digest(Path(__file__))}
    try:
        source_hash = source_digest(args.source_pdf)
        manifest = load(args.manifest)
        errors = manifest_errors(manifest, expected)
        if manifest.get('preparation_status') != 'complete' or manifest_source_hash(manifest) != source_hash:
            errors.append('Terminal image manifest/current source PDF hash mismatch')
        if errors:
            raise ValueError('; '.join(errors))
        page_meta = {p['page']: p for p in manifest['pages']}
        for page in page_meta.values():
            for image in [page['original']]+page['images']:
                path = (args.manifest.parent / image['path']).resolve()
                if not path.is_relative_to(args.manifest.parent) or not path.is_file() or digest(path) != image['sha256']:
                    raise ValueError('Prepared source image missing/outside root/hash changed')
        report.update(source_sha256=source_hash, source_pdf=str(args.source_pdf),
                      image_manifest=str(args.manifest), image_manifest_sha256=digest(args.manifest))
        pages, provenance, seen_ids = {}, {}, set()
        covers = {}
        if args.covers:
            from prepare_covers import load_cover_manifest, minimal_cover_page
            covers = load_cover_manifest(args.covers, source_hash, expected)
            report['retained_cover_pages'] = sorted(covers)
        body_expected = [n for n in expected if n not in covers]
        if args.pages_dir:
            # A new run may have only body OCR pages; existing all-page indexes
            # remain admissible, with covers deliberately replaced by native pages.
            candidates = [args.pages_dir/'manifest.json', args.pages_dir.parent/'manifest.json']
            cover_index_path = next((p for p in candidates if p.is_file()), None)
            declared = sorted(row['page'] for row in load(cover_index_path).get('pages', [])) if cover_index_path else []
            index_scope = body_expected if declared == body_expected else expected
            index_path, entries = canonical_index(args.pages_dir, source_hash, index_scope)
        for number in expected:
            if number in covers:
                data = minimal_cover_page(covers[number], page_meta[number])
                ids = {b['id'] for b in data['blocks']}
                if ids & seen_ids:
                    raise ValueError('Cover placeholder duplicates a body source ID')
                seen_ids.update(ids)
                pages[number], provenance[number] = data, {
                    'input_kind': 'reviewed_cover_without_ocr', 'transcription': False,
                    'cover_manifest': str(args.covers), 'cover_manifest_sha256': digest(args.covers),
                    'review': covers[number]['review']}
                continue
            if args.pages_dir:
                data, origin = read_canonical(args.pages_dir, index_path, entries[number], number, source_hash, page_meta[number])
            else:
                data, origin = read_job(args.jobs_dir, number, source_hash, page_meta[number], expected)
            check_page(data, number, page_meta[number], seen_ids, source_hash)
            pages[number], provenance[number] = data, origin
        parts, parts_by_page = (load_document_parts(args.document_parts, source_hash, page_meta)
                               if args.document_parts else ([], {}))
        report['document_parts'] = parts
        source = source_records(pages, page_meta, provenance, args.manifest.parent, args.output, source_hash, report,
                                parts_by_page)
        reading = reading_records(source, report)
        bind_figures(reading, args.figures, source_hash, page_meta, report)
        bind_table_captions(reading, report)
        reading = apply_footnotes(reading, args.footnote_decisions, source_hash, source, report)
        reading = apply_decisions(reading, args.merge_decisions, source_hash, report)
        if args.label_rules:
            reading, report['inline_label_decisions'] = annotate_inline_labels(reading, load(args.label_rules))
        if args.table_layouts:
            patches = load(args.table_layouts)
            if patches.get('source_sha256') != source_hash:
                raise ValueError('Table ruling patches belong to a different source')
            by_id = {b['block_id']: b for b in reading}
            seen_table_patches = set()
            for patch in patches.get('patches', []):
                identifier = patch['block_id']
                block = by_id.get(identifier)
                if (identifier in seen_table_patches or not block or block['type'] != 'table'
                        or patch['text_sha256'] != text_hash(block['text'])
                        or patch['source_image_sha256'] != block['source_image_sha256']):
                    raise ValueError('Table ruling patch target/hash mismatch: ' + identifier)
                seen_table_patches.add(identifier)
                block['table_layout'] = copy.deepcopy(patch['table_layout'])
            report['table_layout_decisions'] = sorted(seen_table_patches)
        report['errors'].extend(validate_assembly(source, reading))
        if source_digest(args.source_pdf) != source_hash:
            report['errors'].append('Source PDF changed during assembly')
        if report['errors']:
            raise ValueError('Final source accounting/offset/hash validation failed')
        write_jsonl(targets[0], source)
        write_jsonl(targets[1], reading)
        report.update(status='needs_review' if report['needs_review'] else 'assembled', source_blocks=len(source),
                      source_body_blocks=sum(b['type'] not in {'header', 'footer'} for b in source), reading_blocks=len(reading),
                      multi_page_blocks=sum(len({s['page'] for s in b['sources']})>1 for b in reading),
                      footnotes=sum(len(b.get('footnotes', [])) for b in reading),
                      nested_footnote_blocks=sum(len(note['blocks']) for b in reading for note in b.get('footnotes', [])),
                      source_body_ids_covered_exactly_once=True,
                      output_hashes={path.name: digest(path) for path in targets[:2]},
                      input_pages=[provenance[n] for n in expected])
    except Exception as exc:
        report['errors'].append(f'{type(exc).__name__}: {exc}')
        report['status'] = 'failed'
    report.update(wall_seconds=time.perf_counter()-started, ended_unix=time.time())
    atomic_json(targets[2], report)
    print(json.dumps({key: report.get(key) for key in ('status', 'source_blocks', 'reading_blocks', 'multi_page_blocks', 'wall_seconds', 'errors')}))
    raise SystemExit(1 if report['status'] == 'failed' else 0)


if __name__ == '__main__':
    main()
