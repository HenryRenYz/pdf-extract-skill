#!/usr/bin/env python3
"""Plan source-reviewed table widths and adjacent text/table compositions.

This module changes no text, styles, cells or source mappings. The renderer uses
native LaTeX width/minipage syntax. Complete source blocks are consumed once;
actual rendered height and legibility still require PDF visual verification.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from table_data import table_rows


class TablePresentationError(ValueError):
    pass


def _id(block):
    return block.get('block_id') or block.get('id')


def _text_hash(block):
    return sha256(block.get('text', '').encode('utf-8')).hexdigest()


def _source_ids(block):
    return [record['block_id'] for record in block.get('sources', [])] or [_id(block)]


def _positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise TablePresentationError(f'{label} must be finite and positive')
    return float(value)


def _bbox(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise TablePresentationError(f'{label}: a four-coordinate source bbox is required')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise TablePresentationError(f'{label}: source bbox must be finite')
    if value[0] < 0 or value[1] < 0 or value[2] <= value[0] or value[3] <= value[1]:
        raise TablePresentationError(f'{label}: invalid source bbox')
    return list(value)


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _review(record, label):
    review = record.get('review', {})
    evidence = review.get('evidence') or review.get('evidence_path')
    if review.get('source_checked') is not True or not review.get('reviewer') or not evidence:
        raise TablePresentationError(f'{label}: explicit source_checked review with reviewer/evidence is required')
    if review.get('evidence_path'):
        evidence_path = Path(review['evidence_path'])
        if not evidence_path.is_file() or not review.get('evidence_sha256'):
            raise TablePresentationError(f'{label}: source review evidence file/hash is missing')
        if sha256(evidence_path.read_bytes()).hexdigest() != review['evidence_sha256']:
            raise TablePresentationError(f'{label}: source review evidence hash changed')
    return review


def _source_bbox(block, page):
    sources = [s for s in block.get('sources', []) if s.get('page') == page]
    if sources:
        return _union([_bbox(s.get('bbox'), _id(block)) for s in sources])
    if block.get('page') == page:
        return _bbox(block.get('bbox'), _id(block))
    raise TablePresentationError(f'{_id(block)}: no source geometry on the reviewed physical page')



def _validate_columns(table, record, source_hash):
    """Optional native-column hints are bound to the same immutable source."""
    if not isinstance(record, dict):
        raise TablePresentationError('column_layout must be a source-reviewed record')
    ident = _id(table)
    if record.get('table_block_id', record.get('block_id')) != ident or record.get('block_id', ident) != ident:
        raise TablePresentationError(f'{ident}: column layout targets another table')
    if record.get('source_sha256') != source_hash:
        raise TablePresentationError(f'{ident}: column layout PDF source hash mismatch')
    if record.get('source_image_sha256') != table.get('source_image_sha256'):
        raise TablePresentationError(f'{ident}: column layout page image hash changed')
    if record.get('text_sha256') != _text_hash(table):
        raise TablePresentationError(f'{ident}: column layout data/text changed')
    _review(record, ident + ' columns')
    layout = table.get('table_layout', {})
    count = layout.get('column_count')
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise TablePresentationError(f'{ident}: reviewed semantic table column count is missing')
    if record.get('column_count', count) != count:
        raise TablePresentationError(f'{ident}: column layout disagrees with semantic grid column count')
    weights = record.get('column_width_weights')
    if not isinstance(weights, list) or len(weights) != count:
        raise TablePresentationError(f'{ident}: column width weights must cover every semantic grid column')
    for weight in weights:
        _positive(weight, ident + ' column weight')
    if not math.isfinite(sum(weights)):
        raise TablePresentationError(f'{ident}: total column weight must be finite')
    alignment = record.get('column_alignment', 'c')
    if isinstance(alignment, str):
        alignment = [alignment] * count
    if not isinstance(alignment, list) or len(alignment) != count or any(a not in {'c', 'l', 'r'} for a in alignment):
        raise TablePresentationError(f'{ident}: invalid source column alignment')
    if record.get('default_vertical_alignment', 'middle') not in {'top', 'middle', 'bottom'}:
        raise TablePresentationError(f'{ident}: invalid column vertical alignment')
    policy = record.get('column_width_policy')
    if policy not in {'equal', 'source_proportional'}:
        raise TablePresentationError(f'{ident}: unsupported source column width policy')
    if policy == 'equal' and any(not math.isclose(w, weights[0]) for w in weights):
        raise TablePresentationError(f'{ident}: equal source column policy contains unequal weights')
    auxiliary = record.get('auxiliary_columns', [])
    if (not isinstance(auxiliary, list) or
            any(isinstance(c, bool) or not isinstance(c, int) or not 1 <= c < count for c in auxiliary) or
            len(auxiliary) != len(set(auxiliary))):
        raise TablePresentationError(f'{ident}: invalid auxiliary-column index')
    boundaries = {rule.get('boundary') for rule in layout.get('vertical', [])}
    if any(column in boundaries for column in auxiliary):
        raise TablePresentationError(f'{ident}: auxiliary split crosses a real source vertical boundary')
    physical = record.get('physical_column_groups')
    if physical is not None:
        if (not isinstance(physical, list) or not physical or any(not isinstance(group, list) or not group for group in physical) or
                any(isinstance(column, bool) or not isinstance(column, int) for group in physical for column in group) or
                [column for group in physical for column in group] != list(range(count))):
            raise TablePresentationError(f'{ident}: physical column groups must retain every grid column in original order')
        split_members = {column for group in physical for column in group[1:]}
        if split_members != set(auxiliary):
            raise TablePresentationError(f'{ident}: physical groups differ from reviewed auxiliary columns')
        physical_weights = record.get('physical_column_width_weights')
        if not isinstance(physical_weights, list) or len(physical_weights) != len(physical):
            raise TablePresentationError(f'{ident}: physical column weights are incomplete')
        for weight in physical_weights:
            _positive(weight, ident + ' physical column weight')
    topology_hash = record.get('table_line_topology_sha256')
    if topology_hash is not None and topology_hash != sha256(json.dumps(layout, ensure_ascii=False, sort_keys=True).encode()).hexdigest():
        raise TablePresentationError(f'{ident}: original table border topology changed after column review')
    if record.get('semantic_row_count', layout.get('row_count')) != layout.get('row_count'):
        raise TablePresentationError(f'{ident}: source column review has a stale row count')
    return record


def _validate_rendered_adaptation(table, record, source_hash):
    """Reading-layout choices remain separate from immutable source geometry."""
    ident = _id(table)
    if not isinstance(record, dict):
        raise TablePresentationError(f'{ident}: rendered adaptation must be a record')
    if record.get('source_sha256') != source_hash or record.get('text_sha256') != _text_hash(table):
        raise TablePresentationError(f'{ident}: rendered adaptation source/text hash changed')
    count = table.get('table_layout', {}).get('column_count')
    weights = record.get('column_width_weights')
    if not isinstance(weights, list) or len(weights) != count:
        raise TablePresentationError(f'{ident}: rendered widths must cover every grid column')
    for weight in weights:
        _positive(weight, ident + ' rendered column weight')
    if not math.isfinite(sum(weights)):
        raise TablePresentationError(f'{ident}: rendered width total must be finite')
    iteration = record.get('iteration')
    if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 1:
        raise TablePresentationError(f'{ident}: rendered adaptation needs a positive revision iteration')
    if not isinstance(record.get('reason'), str) or not record['reason'].strip():
        raise TablePresentationError(f'{ident}: rendered adaptation reason is required')
    review = record.get('review', {})
    path = Path(review.get('evidence_path') or '')
    if (review.get('rendered_checked') is not True or not review.get('reviewer')
            or not path.is_file() or not review.get('evidence_sha256')):
        raise TablePresentationError(f'{ident}: actual rendered review file/hash is required')
    if sha256(path.read_bytes()).hexdigest() != review['evidence_sha256']:
        raise TablePresentationError(f'{ident}: rendered review evidence changed')
    return record


def build_table_plan(blocks, metadata, *, text_width_mm=164.0, _allow_parts=True):
    """Return presentations, groups/by_anchor/consumed_ids and exact coverage.

    Metadata schema v1 contains source_sha256, tables/presentations (one record per
    source table) and optional text_table groups. Width policies:
    full_width -> text_width_mm; source_width -> width_fraction * text_width_mm;
    natural_width -> width_mm=None (LaTeX measures it, with max_width_mm as cap).
    Group anchors are table owner IDs; member_ids retain original source order,
    including the owned caption. Oversized rendered groups require revising the
    plan and reexporting complete source blocks, never cropping or duplicating.
    """
    text_width_mm = _positive(text_width_mm, 'text_width_mm')
    if metadata.get('schema_version', 1) not in (1, 'table-presentation-v1'):
        raise TablePresentationError('unsupported table presentation schema')
    source_hash = metadata.get('source_sha256')
    if not source_hash or any(block.get('source_sha256') != source_hash for block in blocks):
        raise TablePresentationError('table presentation metadata belongs to another book source')
    ids = [_id(block) for block in blocks]
    if any(not isinstance(ident, str) or not ident for ident in ids) or len(ids) != len(set(ids)):
        raise TablePresentationError('semantic block IDs must be present and unique')
    by_id = dict(zip(ids, blocks))
    positions = {ident: i for i, ident in enumerate(ids)}
    table_ids = {ident for ident, block in by_id.items() if block.get('type') == 'table'}
    presentations, consumed, groups, warnings = {}, set(), [], []
    records = metadata.get('tables', metadata.get('presentations', []))
    if isinstance(records, dict):
        records = [{**record, 'block_id': ident} for ident, record in records.items()]
    if not isinstance(records, list):
        raise TablePresentationError('presentations must be a list or block-ID mapping')
    for record in records:
        ident = record.get('block_id')
        if ident not in table_ids or ident in presentations:
            raise TablePresentationError(f'{ident}: missing, duplicate or non-table presentation target')
        table = by_id[ident]
        review = _review(record, ident)
        page = record.get('physical_page')
        if isinstance(page, bool) or not isinstance(page, int) or page != table.get('page'):
            raise TablePresentationError(f'{ident}: physical page does not match the source table')
        if record.get('source_image_sha256') != table.get('source_image_sha256'):
            raise TablePresentationError(f'{ident}: reviewed page image hash is stale')
        if record.get('text_sha256') != _text_hash(table):
            raise TablePresentationError(f'{ident}: table data/text changed since source review')
        if record.get('source_sha256', source_hash) != source_hash:
            raise TablePresentationError(f'{ident}: per-table source PDF hash mismatch')
        box = _bbox(record.get('source_bbox_px'), ident)
        if box != _source_bbox(table, page):
            raise TablePresentationError(f'{ident}: source table geometry changed')
        body_basis = None
        reference_id = record.get('body_reference_block_id')
        if reference_id is not None:
            reference = by_id.get(reference_id)
            if not reference or reference.get('page') != page:
                raise TablePresentationError(f'{ident}: source body-width reference is missing or on another page')
            if record.get('body_reference_text_sha256') != _text_hash(reference):
                raise TablePresentationError(f'{ident}: source body-width reference text changed')
            reference_box = _bbox(record.get('body_reference_bbox'), ident)
            if reference_box != _source_bbox(reference, page):
                raise TablePresentationError(f'{ident}: source body-width reference geometry changed')
            body_basis = reference_box[2] - reference_box[0]
        caption_id = record.get('caption_block_id')
        if record.get('caption_owner_block_id', ident) != ident or caption_id != table.get('caption_block_id'):
            raise TablePresentationError(f'{ident}: incorrect source caption owner')
        if caption_id:
            caption = by_id.get(caption_id)
            if not caption or caption.get('type') != 'caption' or caption.get('page') != page:
                raise TablePresentationError(f'{ident}: source caption is missing or on another page')
            if record.get('caption_text_sha256') != _text_hash(caption):
                raise TablePresentationError(f'{ident}: owned caption text changed')
            if record.get('caption_bbox') is not None and record['caption_bbox'] != _source_bbox(caption, page):
                raise TablePresentationError(f'{ident}: owned caption source geometry changed')
            if caption.get('source_image_sha256') != record['source_image_sha256']:
                raise TablePresentationError(f'{ident}: caption source page hash changed')
        policy = record.get('width_policy')
        if policy == 'full_width':
            width_mm = text_width_mm
        elif policy == 'natural_width':
            width_mm = None
        elif policy == 'source_width':
            fraction = _positive(record.get('width_fraction', record.get('width_fraction_of_textwidth')), f'{ident} width_fraction')
            if fraction > 1:
                raise TablePresentationError(f'{ident}: width_fraction exceeds the text width')
            if body_basis is not None and not math.isclose(fraction, (box[2] - box[0]) / body_basis, rel_tol=1e-6, abs_tol=1e-8):
                raise TablePresentationError(f'{ident}: table width fraction differs from its reviewed source body reference')
            width_mm = fraction * text_width_mm
        else:
            raise TablePresentationError(f'{ident}: unsupported width policy')
        normalized_parts = []
        if record.get('table_parts') is not None:
            parts = record['table_parts']
            if not _allow_parts or not isinstance(parts, list) or len(parts) < 2:
                raise TablePresentationError(f'{ident}: multipart tables require at least two nonnested source-reviewed parts')
            cursor, boxes = 0, []
            for ordinal, part in enumerate(parts):
                bounds = part.get('text_range')
                if (not isinstance(bounds, list) or len(bounds) != 2 or any(type(v) is not int for v in bounds)
                        or not 0 <= cursor <= bounds[0] < bounds[1] <= len(table['text'])
                        or table['text'][cursor:bounds[0]].strip()):
                    raise TablePresentationError(f'{ident}: table-part ranges must cover the complete original text exactly once')
                fragment = table['text'][bounds[0]:bounds[1]]
                if (part.get('text_sha256') != sha256(fragment.encode()).hexdigest()
                        or len(re.findall(r'<table\b', fragment, re.I)) != 1
                        or not re.fullmatch(r'\s*<table\b[^>]*>.*</table>\s*', fragment, re.I | re.S)):
                    raise TablePresentationError(f'{ident}: stale or malformed single HTML table part')
                part_box = _bbox(part.get('source_bbox_px'), ident + ' part')
                if not (box[0] <= part_box[0] < part_box[2] <= box[2] and box[1] <= part_box[1] < part_box[3] <= box[3]):
                    raise TablePresentationError(f'{ident}: table-part source bbox lies outside reviewed owner')
                projected = deepcopy(table)
                projected.update(text=fragment, styles=[], bbox=part_box,
                                 table_layout=part.get('table_layout'), caption_block_id=None)
                projected.pop('cells', None)
                projected['sources'] = [{**source, 'bbox': part_box} for source in table.get('sources', [])]
                nested_record = part.get('presentation')
                if not isinstance(nested_record, dict) or nested_record.get('table_parts') is not None:
                    raise TablePresentationError(f'{ident}: each part requires a standard reviewed presentation')
                nested_plan = build_table_plan([projected], {'source_sha256': source_hash, 'tables': [nested_record]},
                                              text_width_mm=text_width_mm, _allow_parts=False)
                normalized = nested_plan['presentations'][ident]
                if normalized['width_mm'] is None:
                    raise TablePresentationError(f'{ident}: multipart columns require explicit reviewed widths')
                part_basis = (box[2] - box[0]) if policy == 'full_width' else body_basis
                if part_basis is not None and not math.isclose(normalized['width_mm'] / text_width_mm,
                        (part_box[2] - part_box[0]) / part_basis, rel_tol=1e-6, abs_tol=1e-8):
                    raise TablePresentationError(f'{ident}: part width differs from reviewed source/body scale')
                normalized_parts.append({**part, 'presentation': normalized, 'ordinal': ordinal})
                boxes.append(part_box)
                cursor = bounds[1]
            if table['text'][cursor:].strip():
                raise TablePresentationError(f'{ident}: unconsumed text follows table parts')
            for left, right in zip(boxes, boxes[1:]):
                if left[2] > right[0] or min(left[3], right[3]) <= max(left[1], right[1]):
                    raise TablePresentationError(f'{ident}: parts must form a source-supported left-to-right row')
            gap_fraction = record.get('parts_gap_fraction')
            if type(gap_fraction) not in (int, float) or not math.isfinite(gap_fraction) or not 0 <= gap_fraction < 1:
                raise TablePresentationError(f'{ident}: table part gap must be finite and nonnegative')
            gap_basis = (box[2] - box[0]) if policy == 'full_width' else body_basis
            if len(boxes) == 2 and gap_basis is not None and not math.isclose(
                    gap_fraction, (boxes[1][0] - boxes[0][2]) / gap_basis, rel_tol=1e-6, abs_tol=1e-8):
                raise TablePresentationError(f'{ident}: part gap differs from reviewed source')
            if sum(part['presentation']['width_mm'] for part in normalized_parts) + (len(parts)-1)*gap_fraction*text_width_mm > text_width_mm + 1e-6:
                raise TablePresentationError(f'{ident}: multipart native row exceeds text width')
        column_layout = record.get('column_layout')
        if column_layout is not None and not normalized_parts:
            _validate_columns(table, column_layout, source_hash)
        row_spacing = record.get('row_spacing_pt')
        if record.get('caption_position', 'top') not in {'top', 'bottom'}:
            raise TablePresentationError(f'{ident}: caption_position must be top or bottom')
        row_layout = record.get('row_layout')
        if row_layout is not None:
            offsets = row_layout.get('baseline_offsets_em') if isinstance(row_layout, dict) else None
            count = table.get('table_layout', {}).get('row_count')
            if (not isinstance(offsets, list) or len(offsets) != count or not offsets
                    or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in offsets)
                    or offsets[0] != 0 or any(b <= a for a,b in zip(offsets, offsets[1:]))):
                raise TablePresentationError(f'{ident}: row_layout needs increasing baseline_offsets_em, starting at zero, for every row')
            if row_spacing is not None or normalized_parts:
                raise TablePresentationError(f'{ident}: baseline layout cannot combine with row_spacing_pt or multipart tables')
            # A compressed grid is meaningful for staggered, unmerged cells.
            # Merged cells need their own measured vertical placement.
            if any(cell.rowspan > 1 for row in table_rows(table) for cell in row):
                raise TablePresentationError(f'{ident}: baseline layout does not support row spans')
        if row_spacing is not None:
            row_count = table.get('table_layout', {}).get('row_count')
            if (not isinstance(row_spacing, list) or len(row_spacing) != row_count or
                    any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 24 for v in row_spacing)):
                raise TablePresentationError(f'{ident}: reviewed row spacing must cover every row with finite 0..24pt values')
        adaptation = record.get('rendered_adaptation')
        if adaptation is not None:
            if column_layout is None or policy == 'natural_width':
                raise TablePresentationError(f'{ident}: rendered widths require reviewed fixed-width columns')
            _validate_rendered_adaptation(table, adaptation, source_hash)
        presentations[ident] = {**record, 'width_fraction': record.get('width_fraction', record.get('width_fraction_of_textwidth')), 'width_mm': width_mm, 'max_width_mm': text_width_mm,
                               'source_block_ids': _source_ids(table), 'review': review,
                               'table_parts': normalized_parts, 'parts_gap_mm': record.get('parts_gap_fraction', 0) * text_width_mm}
    if set(presentations) != table_ids:
        missing = sorted(table_ids - set(presentations))
        raise TablePresentationError('every source table needs a reviewed width policy; missing: ' + ', '.join(missing))
    group_ids = set()
    for decision in metadata.get('groups', []):
        gid = decision.get('group_id')
        if not isinstance(gid, str) or not gid or gid in group_ids:
            raise TablePresentationError('missing or duplicate table layout group_id')
        group_ids.add(gid)
        if decision.get('kind') != 'text_table':
            raise TablePresentationError(f'{gid}: only text_table compositions are supported')
        review = _review(decision, gid)
        ident, caption_id = decision.get('table_block_id'), decision.get('caption_block_id')
        if ident not in presentations:
            raise TablePresentationError(f'{gid}: reviewed table presentation is missing')
        presentation = presentations[ident]
        table = by_id[ident]
        page = decision.get('physical_page')
        if page != presentation['physical_page']:
            raise TablePresentationError(f'{gid}: group physical page differs from the table')
        if decision.get('caption_owner_block_id', ident) != ident or caption_id != presentation['caption_block_id'] or not caption_id:
            raise TablePresentationError(f'{gid}: table group must include its correct owned caption')
        if review.get('source_image_sha256') != table.get('source_image_sha256'):
            raise TablePresentationError(f'{gid}: group source page image hash is stale')
        table_box = _bbox(decision.get('source_table_bbox_px'), gid)
        if table_box != presentation['source_bbox_px']:
            raise TablePresentationError(f'{gid}: table bbox differs from its source review')
        text_records = decision.get('text_blocks', [])
        if not isinstance(text_records, list) or not text_records:
            raise TablePresentationError(f'{gid}: complete adjacent text blocks are required')
        member_ids, text_ids, text_boxes = [caption_id, ident], [], []
        for descriptor in text_records:
            tid = descriptor.get('block_id')
            text = by_id.get(tid)
            if not text or text.get('type') not in {'paragraph', 'formula', 'example', 'proof', 'theorem'}:
                raise TablePresentationError(f'{gid}: text side contains a missing or non body/formula block')
            if descriptor.get('text_sha256') != _text_hash(text):
                raise TablePresentationError(f'{gid}: complete text/formula changed since source review')
            if descriptor.get('source_block_ids') != _source_ids(text):
                raise TablePresentationError(f'{gid}: text source provenance changed')
            source_box = _bbox(descriptor.get('source_bbox_px'), tid)
            if source_box != _source_bbox(text, page):
                raise TablePresentationError(f'{gid}: complete text block source geometry changed')
            own_sources = text.get('sources', []) or [text]
            reviewed_sources = {(s.get('page'), s.get('source_image_sha256'))
                                for s in descriptor.get('reviewed_text_sources', [])}
            for source in own_sources:
                source_page, image_hash = source.get('page'), source.get('source_image_sha256')
                if source_page == page:
                    if image_hash != review['source_image_sha256']:
                        raise TablePresentationError(f'{gid}: text source page hash differs from the reviewed table page')
                elif (source_page, image_hash) not in reviewed_sources:
                    raise TablePresentationError(f'{gid}: every merged continuation source page requires review')
            text_ids.append(tid)
            text_boxes.append(source_box)
            member_ids.append(tid)
        if text_ids != sorted(text_ids, key=positions.__getitem__):
            raise TablePresentationError(f'{gid}: text blocks must retain original semantic order')
        text_box = _bbox(decision.get('source_text_bbox_px'), gid)
        if text_box != _union(text_boxes):
            raise TablePresentationError(f'{gid}: text side bbox must cover complete reviewed text blocks')
        side = decision.get('source_position')
        if side not in {'left', 'right'}:
            raise TablePresentationError(f'{gid}: declared source table side must be left or right')
        for box in text_boxes:
            if ((side == 'left' and table_box[2] > box[0]) or
                    (side == 'right' and box[2] > table_box[0])):
                raise TablePresentationError(f'{gid}: source geometry does not support the declared table side')
        if min(table_box[3], text_box[3]) <= max(table_box[1], text_box[1]):
            raise TablePresentationError(f'{gid}: source table/text have no vertical overlap')
        render_side = decision.get('render_position', side)
        if render_side != side:
            raise TablePresentationError(f'{gid}: render side must match the reviewed source layout')
        gap_fraction = _positive(decision.get('gap_width_fraction', decision.get('gap_fraction_of_textwidth')), f'{gid} gap_width_fraction')
        if gap_fraction >= 1:
            raise TablePresentationError(f'{gid}: invalid table/text gap fraction')
        width_mm = presentation['width_mm']
        if width_mm is None:
            raise TablePresentationError(f'{gid}: text_table groups require a source-width numeric table width')
        gap_mm = gap_fraction * text_width_mm
        text_mm = text_width_mm - width_mm - gap_mm
        if text_mm <= 0:
            raise TablePresentationError(f'{gid}: table and source gap leave no space for complete text')
        declared_text_ids = decision.get('text_block_ids')
        if declared_text_ids is not None and declared_text_ids != text_ids:
            raise TablePresentationError(f'{gid}: text block list differs from complete descriptors')
        member_hashes = decision.get('text_sha256')
        if member_hashes is not None:
            if not isinstance(member_hashes, dict) or set(member_hashes) != set(member_ids):
                raise TablePresentationError(f'{gid}: per-member text hashes must cover every group member')
            if any(member_hashes[bid] != _text_hash(by_id[bid]) for bid in member_ids):
                raise TablePresentationError(f'{gid}: group member text/data changed')
        member_boxes = decision.get('member_bboxes')
        if member_boxes is not None:
            if set(member_boxes) != set(member_ids) or any(member_boxes[bid] != _source_bbox(by_id[bid], page) for bid in member_ids):
                raise TablePresentationError(f'{gid}: group member geometry changed')
        member_sources = decision.get('member_source_block_ids')
        if member_sources is not None:
            if set(member_sources) != set(member_ids) or any(member_sources[bid] != _source_ids(by_id[bid]) for bid in member_ids):
                raise TablePresentationError(f'{gid}: group member source mapping changed')
        basis = decision.get('source_body_width_px')
        if basis is not None:
            basis = _positive(basis, f'{gid} source_body_width_px')
            actual_gap = text_box[0] - table_box[2] if side == 'left' else table_box[0] - text_box[2]
            if not math.isclose(gap_fraction, actual_gap / basis, rel_tol=1e-6, abs_tol=1e-8):
                raise TablePresentationError(f'{gid}: gap fraction differs from reviewed source geometry')
            if not math.isclose(width_mm / text_width_mm, (table_box[2] - table_box[0]) / basis, rel_tol=1e-6, abs_tol=1e-8):
                raise TablePresentationError(f'{gid}: table width fraction differs from reviewed source geometry')
        indices = sorted(positions[bid] for bid in member_ids)
        if len(set(member_ids)) != len(member_ids) or set(indices) != set(range(indices[0], indices[-1] + 1)):
            raise TablePresentationError(f'{gid}: grouping crosses an unconsumed block or duplicates a member')
        if consumed.intersection(member_ids):
            raise TablePresentationError(f'{gid}: source block is consumed by multiple groups')
        ordered = [ids[index] for index in indices]
        declared_members = decision.get('member_ids')
        if declared_members is not None and declared_members != ordered:
            raise TablePresentationError(f'{gid}: declared group members change original source order')
        group = {**decision, 'source_anchor_block_id': decision.get('anchor_block_id'), 'anchor_id': ident, 'anchor_block_id': ident, 'member_ids': ordered,
                 'table_side': side, 'text_block_ids': text_ids,
                 'table_width_mm': width_mm, 'text_width_mm': text_mm, 'gap_width_mm': gap_mm,
                 'source_sha256': source_hash, 'non_floating': True,
                 'must_measure_rendered_height': True,
                 'fallback_on_page_height_exceeded': 'review_plan_and_reexport_complete_blocks'}
        groups.append(group)
        consumed.update(member_ids)
    by_anchor = {group['anchor_id']: group for group in groups}
    expansion = []
    for ident in ids:
        if ident in by_anchor:
            expansion.extend(by_anchor[ident]['member_ids'])
        elif ident not in consumed:
            expansion.append(ident)
    if expansion != ids:
        raise TablePresentationError('table presentation plan changes original source order or coverage')
    input_sources = [sid for block in blocks for sid in _source_ids(block)]
    planned_sources = [sid for ident in expansion for sid in _source_ids(by_id[ident])]
    if any(count > 1 for count in Counter(input_sources).values()):
        raise TablePresentationError('input already duplicates source provenance')
    if planned_sources != input_sources:
        raise TablePresentationError('table presentation plan changes source provenance coverage')
    return {'schema_version': 1, 'presentations': presentations, 'groups': groups,
            'by_anchor': by_anchor, 'consumed_ids': sorted(consumed), 'warnings': warnings,
            'source_coverage': {'semantic_block_count': len(ids), 'planned_block_count': len(expansion),
                                'source_reference_count': len(input_sources), 'unchanged': True,
                                'exactly_once': True, 'original_order_preserved': True},
            'limits': 'Widths and complete source consumption are validated; rendered minipage height, table cells and glyph legibility require visual QA.'}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blocks', type=Path, required=True)
    parser.add_argument('--decisions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--text-width-mm', type=float, default=164)
    args = parser.parse_args()
    plan = build_table_plan([json.loads(line) for line in args.blocks.read_text().splitlines() if line.strip()],
                            json.loads(args.decisions.read_text()), text_width_mm=args.text_width_mm)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'table_count': len(plan['presentations']), 'group_count': len(plan['groups']),
                      'source_coverage': plan['source_coverage']}, ensure_ascii=False))
