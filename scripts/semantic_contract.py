"""Shared, source-traceable structural metadata contract.

Coordinates in style spans are Unicode code points, never UTF-8 bytes.  These
checks establish representation integrity, not source transcription accuracy.
Absent presentation metadata remains legacy input; explicit unknown boundaries
remain unresolved and are never silently converted into new paragraphs.
"""
import hashlib
import math
import re
from pathlib import Path
from table_data import table_rows

TYPES = {'heading', 'paragraph', 'formula', 'table', 'figure', 'caption',
         'theorem', 'proof', 'example', 'exercise', 'header', 'footer', 'list',
         'code', 'grammar', 'display_group', 'blank'}
STYLES = {'normal', 'bold', 'italic', 'bold_italic', 'upright', 'bold_math',
          'double_struck', 'underline', 'kai', 'monospace', 'color'}
STYLE_EVIDENCE = {'unreviewed', 'source_checked', 'uncertain', 'rule_inferred'}
SEMANTIC_FIELDS = ('paragraph_presentation', 'heading_presentation', 'list_presentation',
                   'code_presentation', 'grammar_presentation', 'group_presentation',
                   'items', 'groups', 'annotations', 'lines', 'source_line_ids',
                   'caption_block_id', 'list_kind', 'ordered', 'latex', 'native_latex',
                   'alternatives', 'left_delimiter', 'right_delimiter', 'cells',
                   'grammar_lines', 'flow_presentation')
HEADING_ROLES = {'local_heading', 'algorithm_label', 'chapter', 'section', 'subsection',
                 'subsubsection', 'part', 'document_title', 'unnumbered_heading'}


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def label_marker_identity(value):
    """Preserve marker content while allowing reviewed presentation changes.

    Numeric labels may change their wrapper, e.g. (1), （1）, 1. or 1; the
    digits stay identical. Other labels permit spacing/paren-width changes.
    NFKC is deliberately avoided because it changes unrelated characters.
    """
    if not isinstance(value, str):
        return None
    compact = re.sub(r'\s+', '', value.translate(str.maketrans({'（': '(', '）': ')'})))
    numbered = re.fullmatch(r'\(([0-9]+)\)|([0-9]+)[.．、]?', compact)
    if numbered:
        return ('number', numbered.group(1) or numbered.group(2))
    return ('literal', compact)


def project_label_styles(original, replacement, styles):
    """Transport label styles only when Unicode offsets have a unique mapping.

    Width-only edits keep all offsets. A whole-label style can follow a changed
    wrapper length; a partial range needs its own explicit source review.
    """
    if not isinstance(styles, list) or any(not isinstance(style, dict) for style in styles):
        raise ValueError('label_styles must be a list of style objects')
    if len(original) == len(replacement):
        return [dict(style) for style in styles]
    if any(style.get('start') != 0 or style.get('end') != len(original) for style in styles):
        raise ValueError('length-changing label normalization cannot project partial label_styles')
    return [{**style, 'end': len(replacement)} for style in styles]


def _choice(value, options):
    return isinstance(value, str) and value in options


def validate_style_spans(styles, text):
    errors = []
    if not isinstance(styles, list):
        return ['styles must be a list']
    for style in styles:
        if (not isinstance(style, dict) or not _choice(style.get('kind'), STYLES)
                or not _choice(style.get('evidence'), STYLE_EVIDENCE)
                or type(style.get('start')) is not int or type(style.get('end')) is not int
                or not 0 <= style['start'] < style['end'] <= len(text)):
            errors.append('invalid Unicode style span/evidence')
        elif style['evidence'] == 'rule_inferred' and any(
                not isinstance(style.get(k), str) or not style[k].strip()
                for k in ('rule_id', 'rule_version')):
            errors.append('inferred style lacks rule ID/version')
    try:
        color_spans(styles)
    except ValueError as error:
        errors.append(str(error))
    return errors


def color_spans(styles):
    """Validate explicit source RGB spans; never infer syntax highlighting."""
    colors = [s for s in styles if isinstance(s, dict) and s.get('kind') == 'color']
    for span in colors:
        if not isinstance(span.get('value'), str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', span['value']):
            raise ValueError('color style requires value=#RRGGBB')
        for other in colors:
            if (type(span.get('start')) is int and type(span.get('end')) is int
                    and type(other.get('start')) is int and type(other.get('end')) is int
                    and max(span['start'], other['start']) < min(span['end'], other['end'])
                    and span['value'].upper() != other['value'].upper()):
                raise ValueError('overlapping source color spans disagree')
    return colors


def span_color(styles, start, end):
    return next((s['value'][1:].upper() for s in color_spans(styles)
                 if s['start'] <= start and s['end'] >= end), None)


def _number(value, low=0, high=6):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def validate_grammar_lines(block):
    rows = block['grammar_lines']
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or not isinstance(row.get('text'), str) for row in rows):
        raise ValueError('grammar_lines requires literal source rows')
    if '\n'.join(row['text'] for row in rows) != block['text'].rstrip('\n'):
        raise ValueError('grammar_lines must preserve every original line and character')
    for row_index, row in enumerate(rows):
        if not _number(row.get('indent_em', 0), high=40):
            raise ValueError('grammar indent_em must be finite and within 0..40')
        if '\n' in row['text'] or '\r' in row['text'] or '\t' in row['text']:
            raise ValueError('grammar_lines needs separate literal rows; tabs require an explicit native layout')
        anchor = row.get('align_to')
        if anchor is not None:
            if (not isinstance(anchor, dict) or type(anchor.get('line')) is not int
                    or not 0 <= anchor['line'] < row_index or type(anchor.get('offset')) is not int
                    or not 0 <= anchor['offset'] <= len(rows[anchor['line']]['text'])
                    or rows[anchor['line']].get('align_to') is not None
                    or rows[anchor['line']].get('scripts')):
                raise ValueError('grammar align_to requires a prior unscripted, unanchored row and a Unicode prefix offset')
        scripts = row.get('scripts', [])
        if not isinstance(scripts, list) or any(not isinstance(s, dict) for s in scripts):
            raise ValueError('grammar scripts must be a list of position ranges')
        cursor = 0
        for script in scripts:
            a, b = script.get('start'), script.get('end')
            if (type(a) is not int or type(b) is not int or not cursor <= a < b <= len(row['text'])
                    or script.get('position') not in {'superscript', 'subscript'}):
                raise ValueError('grammar scripts require ordered disjoint Unicode ranges and a position')
            cursor = b


def _review(metadata, block, source_sha256, image_sha256, errors, name):
    """Review assertions require current identities and a hash-checked file."""
    source = source_sha256 or block.get('source_sha256', block.get('source_pdf_sha256'))
    image = image_sha256 or block.get('source_image_sha256')
    for key, expected in (('source_sha256', source), ('source_image_sha256', image),
                          ('text_sha256', text_hash(block['text']))):
        if key in metadata and (not isinstance(metadata[key], str) or len(metadata[key]) != 64
                                or any(c not in '0123456789abcdef' for c in metadata[key])
                                or expected is not None and metadata[key] != expected):
            errors.append(name + ': ' + key + ' mismatch/invalid')
    review = metadata.get('review')
    if review is None:
        return
    if not isinstance(review, dict) or type(review.get('source_checked')) is not bool:
        errors.append(name + ': review needs boolean source_checked')
        return
    if review['source_checked'] is not True:
        return
    source = source_sha256 or block.get('source_sha256', block.get('source_pdf_sha256'))
    image = image_sha256 or block.get('source_image_sha256')
    for key, expected in (('source_sha256', source), ('source_image_sha256', image),
                          ('text_sha256', text_hash(block['text']))):
        if not expected or metadata.get(key) != expected:
            errors.append(name + ': reviewed ' + key + ' mismatch/missing')
    if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        errors.append(name + ': review requires reviewer identity')
    evidence = review.get('evidence_path')
    if not isinstance(evidence, str) or not evidence.strip():
        errors.append(name + ': review requires evidence_path')
        return
    path = Path(evidence).expanduser()
    if not path.is_file():
        errors.append(name + ': review evidence file missing')
    else:
        expected = review.get('evidence_sha256', image)
        if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(name + ': review evidence hash changed')
    owned = metadata.get('source_block_ids')
    if owned is not None and owned != (block.get('text_source_block_ids') or block.get('source_block_ids') or [block.get('block_id', block.get('id'))]):
        errors.append(name + ': source block ownership changed')


def _items(items, errors, prefix='items', depth=0):
    if not isinstance(items, list):
        errors.append(prefix + ': must be a list')
        return
    if depth > 20:
        errors.append(prefix + ': excessive nesting')
        return
    for index, item in enumerate(items):
        name = f'{prefix}[{index}]'
        if isinstance(item, str):
            continue
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            errors.append(name + ': requires text string')
            continue
        for key in ('label', 'marker'):
            if key in item and not isinstance(item[key], str):
                errors.append(name + ': ' + key + ' must be a string')
        if 'styles' in item:
            errors.extend(name + ': ' + e for e in validate_style_spans(item['styles'], item['text']))
        if 'label_styles' in item:
            if not isinstance(item.get('label'), str):
                errors.append(name + ': label_styles requires explicit label')
            else:
                errors.extend(name + '.label: ' + e for e in validate_style_spans(item['label_styles'], item['label']))
        if 'children' in item:
            _items(item['children'], errors, name + '.children', depth + 1)


def _annotations(annotations, block, errors):
    if not isinstance(annotations, list):
        errors.append('annotations must be a list')
        return
    lines = block['text'].splitlines() or ['']
    line_ids = block.get('source_line_ids')
    for item in annotations:
        if not isinstance(item, dict):
            errors.append('annotation must be an object')
            continue
        owner = item.get('owner_block_id', block.get('block_id', block.get('id')))
        if owner != block.get('block_id', block.get('id')):
            errors.append('annotation owner block differs')
        start, end = item.get('start_line'), item.get('end_line')
        if start is not None or end is not None:
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                errors.append('annotation source line range invalid')
        elif 'start_line_id' in item or 'end_line_id' in item:
            if (not isinstance(line_ids, list) or item.get('start_line_id') not in line_ids
                    or item.get('end_line_id') not in line_ids
                    or line_ids.index(item['start_line_id']) > line_ids.index(item['end_line_id'])):
                errors.append('annotation source line ID range invalid')
        else:
            errors.append('annotation requires source line range')
        if 'side' in item and not _choice(item['side'], {'left', 'right', 'above', 'below'}):
            errors.append('annotation side invalid')
        for key, low, high in (('x_offset_ccwd', -6, 6),
                               ('start_yshift_em', -3, 3), ('end_yshift_em', -3, 3)):
            if key in item and not _number(item[key], low, high):
                errors.append('annotation ' + key + ' invalid')
        for key in ('label_block_id', 'label'):
            if key in item and not isinstance(item[key], str):
                errors.append('annotation ' + key + ' must be a string')


def validate_semantic_block(block, source_sha256=None, source_image_sha256=None):
    errors = []
    if not isinstance(block, dict) or not isinstance(block.get('text'), str):
        return ['semantic block requires text string']
    errors.extend(validate_style_spans(block.get('styles', []), block['text']))
    flow = block.get('flow_presentation')
    if flow is not None:
        if not isinstance(flow, dict) or flow.get('schema_version') != 1 or type(flow.get('keep_with_next')) is not bool:
            errors.append('flow_presentation requires schema_version=1 and boolean keep_with_next')
        else:
            _review(flow, block, source_sha256, source_image_sha256, errors, 'flow_presentation')
    if block.get('type') == 'table' and block.get('cells'):
        try:
            for row in table_rows(block):
                for cell in row:
                    errors.extend(validate_style_spans(cell.styles or [], cell.text))
        except ValueError as exc:
            errors.append('table cells: ' + str(exc))
    if 'text_sha256' in block and block['text_sha256'] != text_hash(block['text']):
        errors.append('block text_sha256 changed')
    for key, expected in (('source_sha256', source_sha256),
                          ('source_pdf_sha256', source_sha256),
                          ('source_image_sha256', source_image_sha256)):
        if key in block and expected is not None and block[key] != expected:
            errors.append('block ' + key + ' differs from current source')
    if 'block_id' in block and 'id' in block and block['block_id'] != block['id']:
        errors.append('block_id differs from original id')
    for name in ('paragraph_presentation', 'heading_presentation', 'list_presentation',
                 'code_presentation', 'grammar_presentation', 'group_presentation'):
        metadata = block.get(name)
        if metadata is None:
            continue
        if not isinstance(metadata, dict) or type(metadata.get('schema_version')) is not int or metadata['schema_version'] != 1:
            errors.append(name + ': schema_version must be 1')
            continue
        _review(metadata, block, source_sha256, source_image_sha256, errors, name)
        if name == 'paragraph_presentation':
            if not _choice(metadata.get('boundary_before'), {'continue', 'new_paragraph', 'unknown'}):
                errors.append(name + ': invalid boundary_before')
            if not _choice(metadata.get('first_line'), {'inherit', 'indent', 'noindent'}):
                errors.append(name + ': invalid first_line')
            for key in ('paragraph_id', 'predecessor_block_id', 'successor_block_id'):
                if key in metadata and (not isinstance(metadata[key], str) or not metadata[key]):
                    errors.append(name + ': invalid ' + key)
        elif name == 'heading_presentation':
            if block.get('type') != 'heading' or not _choice(metadata.get('role'), HEADING_ROLES):
                errors.append(name + ': invalid heading role/type')
            if not _choice(metadata.get('font_size'), {'body', 'small_heading', 'chapter', 'section', 'subsection'}):
                errors.append(name + ': invalid font_size')
            if not _number(metadata.get('indent_ccwd', 0)):
                errors.append(name + ': invalid indent_ccwd')
            if 'depth' in metadata and (type(metadata['depth']) is not int or not 1 <= metadata['depth'] <= 6):
                errors.append(name + ': depth must be an integer in 1..6')
            if 'container_id' in metadata and ('depth' not in metadata
                    or not isinstance(metadata['container_id'], str) or not metadata['container_id'].strip()):
                errors.append(name + ': container_id requires explicit depth and a nonempty identifier')
            normalization = metadata.get('label_normalization')
            if normalization is not None:
                if not isinstance(normalization, dict):
                    errors.append(name + ': label normalization must be an object')
                else:
                    original, replacement = normalization.get('original'), normalization.get('replacement')
                    if (not isinstance(original, str) or not original
                            or not isinstance(replacement, str) or not replacement
                            or not block['text'].startswith(original)
                            or len(original) != len(replacement)
                            or label_marker_identity(original) != label_marker_identity(replacement)
                            or any(not isinstance(normalization.get(k), str) or not normalization[k].strip()
                                   for k in ('rule_id', 'rule_version'))):
                        errors.append(name + ': label normalization requires an equal-length original marker prefix and rule identity')
        elif name == 'list_presentation':
            if not _choice(block.get('type'), {'list', 'paragraph'}):
                errors.append(name + ': expected list/paragraph type')
            for key in ('indent_ccwd', 'outer_indent_ccwd', 'hanging_ccwd', 'labelsep_ccwd', 'labelwidth_ccwd', 'item_indent_ccwd', 'label_body_gap_ccwd'):
                if key in metadata and not _number(metadata[key]):
                    errors.append(name + ': invalid ' + key)
            if all(_number(metadata.get(k, 0)) for k in ('indent_ccwd', 'outer_indent_ccwd', 'hanging_ccwd')):
                if metadata.get('indent_ccwd', metadata.get('outer_indent_ccwd', 0)) + metadata.get('hanging_ccwd', 0) > 8:
                    errors.append(name + ': indentation exceeds readable width')
            if 'kind' in metadata and not _choice(metadata['kind'], {'ordered', 'unordered', 'description', 'steps'}):
                errors.append(name + ': invalid list kind')
            if 'label_normalizations' in metadata:
                normalizations, items = metadata['label_normalizations'], block.get('items')
                if not isinstance(normalizations, list) or not isinstance(items, list):
                    errors.append(name + ': label normalizations require structured items/list')
                else:
                    seen_indices = set()
                    for normalization in normalizations:
                        if not isinstance(normalization, dict):
                            errors.append(name + ': label normalization must be an object')
                            continue
                        index = normalization.get('item_index')
                        if (type(index) is not int or not 0 <= index < len(items)
                                or index in seen_indices or not isinstance(items[index], dict)
                                or not isinstance(items[index].get('label'), str)
                                or normalization.get('original') != items[index]['label']):
                            errors.append(name + ': label normalization index/original differs')
                        else:
                            seen_indices.add(index)
                            if isinstance(normalization.get('replacement'), str):
                                try:
                                    project_label_styles(items[index]['label'], normalization['replacement'],
                                                         items[index].get('label_styles', []))
                                except ValueError as error:
                                    errors.append(name + ': ' + str(error))
                        if (isinstance(normalization.get('original'), str)
                                and isinstance(normalization.get('replacement'), str)
                                and label_marker_identity(normalization['original'])
                                != label_marker_identity(normalization['replacement'])):
                            errors.append(name + ': label normalization changes marker identity')
                        if any(not isinstance(normalization.get(key), str) or not normalization[key].strip()
                               for key in ('replacement', 'rule_id', 'rule_version')):
                            errors.append(name + ': label normalization requires replacement and rule identity')
            # An explicit list is one semantic group even when its items came
            # from different scan pages. Compare the rendered labels only;
            # never normalize body text, program literals or mathematical text.
            items = block.get('items')
            if isinstance(items, list):
                replacements = {n.get('item_index'): n.get('replacement')
                    for n in metadata.get('label_normalizations', []) if isinstance(n, dict)} if isinstance(metadata.get('label_normalizations', []), list) else {}
                widths = set()
                for index, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    label = replacements.get(index, item.get('label', ''))
                    if isinstance(label, str):
                        if re.fullmatch(r'\([0-9]+\)', label.strip()): widths.add('ascii')
                        if re.fullmatch(r'（[0-9]+）', label.strip()): widths.add('cjk')
                if len(widths) > 1:
                    errors.append(name + ': one numbered list mixes ASCII/CJK parentheses; requires reviewed label_normalizations')
        elif name == 'code_presentation':
            if block.get('type') != 'code':
                errors.append(name + ': requires code type')
            if not isinstance(metadata.get('language'), str) or not metadata['language'].strip():
                errors.append(name + ': requires language (unknown is allowed)')
            if not _choice(metadata.get('completeness'), {'fragment', 'complete', 'pseudocode', 'unknown'}):
                errors.append(name + ': invalid completeness')
            if not _number(metadata.get('outer_indent_ccwd', 0)):
                errors.append(name + ': invalid outer_indent_ccwd')
            if not _choice(metadata.get('font_policy', 'source'), {'source', 'inherit', 'monospace', 'proportional', 'native'}):
                errors.append(name + ': invalid font_policy')
            wrap = metadata.get('wrap', metadata.get('wrap_policy', 'none'))
            if not _choice(wrap, {'none', 'soft', 'source', 'preserve'}):
                errors.append(name + ': invalid wrap policy')
            tabsize = metadata.get('tabsize')
            if tabsize is not None and (type(tabsize) is not int or not 1 <= tabsize <= 32):
                errors.append(name + ': invalid source tabsize')
            if 'annotations' in metadata:
                _annotations(metadata['annotations'], block, errors)
    if block.get('type') == 'code' and 'code_presentation' not in block:
        errors.append('code requires code_presentation')
    if 'grammar_lines' in block:
        if block.get('type') != 'grammar':
            errors.append('grammar_lines requires grammar type')
        else:
            try:
                validate_grammar_lines(block)
            except ValueError as error:
                errors.append(str(error))
    if block.get('type') == 'grammar' and any(c in block['text'] for c in '⎧⎨⎩⎫⎬⎭⎡⎢⎣⎤⎥⎦'):
        errors.append('grammar delimiters require native LaTeX, not Unicode character art')
    for key in ('latex', 'native_latex'):
        if key in block and not isinstance(block[key], str):
            errors.append(key + ' must be an explicit native LaTeX string')
    if 'alternatives' in block:
        if block.get('type') != 'grammar' or not isinstance(block['alternatives'], list) or not block['alternatives']:
            errors.append('grammar alternatives require a nonempty list')
        else:
            for alternative in block['alternatives']:
                if not isinstance(alternative, dict) or not isinstance(alternative.get('text'), str):
                    errors.append('grammar alternative requires literal text')
                elif 'keyword' in alternative and type(alternative['keyword']) is not bool:
                    errors.append('grammar alternative keyword must be boolean')
        if not _choice(block.get('left_delimiter', r'\{'), {r'\{', '[', '(', '.'}):
            errors.append('grammar left delimiter must be a native LaTeX delimiter')
        if not _choice(block.get('right_delimiter', '.'), {r'\}', ']', ')', '.'}):
            errors.append('grammar right delimiter must be a native LaTeX delimiter')
    if 'items' in block:
        if block.get('type') != 'list':
            errors.append('items require list type')
        _items(block['items'], errors)
    if 'source_line_ids' in block:
        ids = block['source_line_ids']
        if not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)) or len(ids) != len(block['text'].splitlines() or ['']):
            errors.append('source_line_ids must uniquely cover original lines')
    if 'lines' in block and block['lines'] != block['text'].splitlines():
        errors.append('lines differ from original text lines')
    if 'annotations' in block:
        _annotations(block['annotations'], block, errors)
    if 'groups' in block:
        groups = block['groups']
        if not isinstance(groups, list):
            errors.append('groups must be a list')
        else:
            for group in groups:
                if not isinstance(group, dict):
                    errors.append('group must be an object')
                    continue
                if 'text' in group and not isinstance(group['text'], str):
                    errors.append('group native text must be a string')
                for key in ('block_ids', 'caption_block_ids', 'source_block_ids'):
                    if key in group and (not isinstance(group[key], list) or any(not isinstance(x, str) or not x for x in group[key]) or len(set(group[key])) != len(group[key])):
                        errors.append('group ' + key + ' requires unique explicit IDs')
                if 'column_weights' in group and (not isinstance(group['column_weights'], list) or not group['column_weights'] or any(not _number(x, 0.001, 100) for x in group['column_weights'])):
                    errors.append('group column_weights invalid')
                if 'columns' in group:
                    columns = group['columns']
                    if not isinstance(columns, list) or not 2 <= len(columns) <= 4:
                        errors.append('group columns require 2-4 columns')
                    else:
                        column_ids = []
                        for column in columns:
                            if not isinstance(column, dict) or ('code_id' in column) == ('block_ids' in column):
                                errors.append('group column requires exactly one of code_id or block_ids')
                                continue
                            ids = [column['code_id']] if 'code_id' in column else column['block_ids']
                            if (not isinstance(ids, list) or not ids
                                    or any(not isinstance(x, str) or not x for x in ids)):
                                errors.append('group column requires nonempty explicit block IDs')
                            else:
                                column_ids.extend(ids)
                            if 'caption_id' in column:
                                if not isinstance(column['caption_id'], str) or not column['caption_id']:
                                    errors.append('group caption_id must be an explicit ID')
                                else:
                                    column_ids.append(column['caption_id'])
                            if 'weight' in column and not _number(column['weight'], 0.001, 100):
                                errors.append('group column weight invalid')
                        if len(column_ids) != len(set(column_ids)):
                            errors.append('group cannot consume the same body/caption block twice')
                if 'gap_ccwd' in group and not _number(group['gap_ccwd']):
                    errors.append('group gap_ccwd invalid')
                if 'caption_alignment' in group and not _choice(group['caption_alignment'], {'natural', 'bottom'}):
                    errors.append('group caption_alignment invalid')
                if 'reading_order' in group and (not isinstance(group['reading_order'], list) or any(not isinstance(x, str) or not x for x in group['reading_order']) or len(set(group['reading_order'])) != len(group['reading_order'])):
                    errors.append('group reading_order requires unique explicit IDs')
    return errors
