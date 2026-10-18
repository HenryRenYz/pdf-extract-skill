"""Source-authenticated mathematics groups for reviewed book layouts.

This optional composer consumes explicit existing block IDs; it never discovers
or merges equations by heuristics. Immutable block text is projected to native
AMS environments only after all source, image, and text identities are checked.
"""
from hashlib import sha256
from pathlib import Path
import re


def _hash(value):
    return sha256(value.encode('utf-8')).hexdigest()


def math_body(block):
    """Return the single mathematics region and its exact surrounding text."""
    matches = list(re.finditer(r'\\\[(.*?)\\\]|\\\((.*?)\\\)', block['text'], re.S))
    if len(matches) != 1:
        raise ValueError('Math composition requires exactly one explicit region per member')
    item = matches[0]
    return (item.group(1) if item.group(1) is not None else item.group(2)).strip(), block['text'][:item.start()].strip(), block['text'][item.end():].strip()


def validate_math_presentation(decision, block_map):
    if decision.get('schema_version') != 1 or decision.get('kind') not in {'aligned_rows', 'labeled_display'}:
        raise ValueError('Unsupported mathematics presentation')
    ids = decision.get('block_ids', [])
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Math members must be explicit, nonempty and unique')
    review = decision.get('review', {})
    if review.get('source_checked') is not True or not review.get('reviewer'):
        raise ValueError('Math presentation requires an actual source review')
    for ident in ids:
        block = block_map.get(ident)
        expected = decision.get('members', {}).get(ident, {})
        if not block or expected.get('text_sha256') != _hash(block['text']):
            raise ValueError('Math member text changed: ' + ident)
        for key in ('source_sha256', 'source_image_sha256'):
            if not block.get(key) or expected.get(key) != block[key]:
                raise ValueError('Math member source identity changed: ' + ident)
        path = Path(expected.get('evidence_path', ''))
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected['source_image_sha256']:
            raise ValueError('Math source image missing or changed: ' + ident)
        math_body(block)
    if len({block_map[i].get('chapter_id') for i in ids}) != 1:
        raise ValueError('Math members span chapters')
    if decision['kind'] == 'labeled_display' and len(ids) != 1:
        raise ValueError('Labeled display requires one member')
    if decision['kind'] == 'aligned_rows':
        rows = decision.get('rows', [])
        if [row.get('block_id') for row in rows] != ids:
            raise ValueError('Aligned rows must own each member exactly once in source order')
        for row in rows:
            body, lead, trail = math_body(block_map[row['block_id']])
            if lead or trail:
                raise ValueError('Aligned member contains text outside mathematics')
            align = row.get('align_at', 0)
            if type(align) is not int or not 0 <= align <= len(body):
                raise ValueError('Alignment offset outside immutable mathematics')
    return decision


def render_math_presentation(decision, block_map, render_text, render_math=lambda s:s):
    """Compose reviewed native AMS math, using caller's normal text/math renderer."""
    validate_math_presentation(decision, block_map)
    if decision['kind'] == 'aligned_rows':
        rows = []
        for row in decision['rows']:
            body, _, _ = math_body(block_map[row['block_id']])
            # Insert one alignment boundary at a reviewed Unicode offset.
            offset = row.get('align_at', 0)
            left, right = render_math(body[:offset]), render_math(body[offset:])
            indent = row.get('indent_em', 0)
            if type(indent) not in (int, float) or not 0 <= indent <= 6:
                raise ValueError('Invalid reviewed row indentation')
            spacing = (r'\hspace{' + f'{indent:g}' + 'em}') if indent else ''
            rows.append(left + '&' + spacing + right)
        return '\\begin{equation*}\n\\begin{aligned}\n' + '\\\\\n'.join(rows) + '\n\\end{aligned}\n\\end{equation*}'
    block = block_map[decision['block_ids'][0]]
    body, label, trailing = math_body(block)
    projected_prefix = decision.get('math_label_prefix')
    if projected_prefix:
        projected_label = decision.get('label', '')
        if (label or not projected_label or not body.startswith(projected_prefix)
                or not re.fullmatch(re.escape(projected_label) + r'(?:\s|\\quad)*', projected_prefix)):
            raise ValueError('Invalid reviewed mathematical label projection')
        body = body[len(projected_prefix):].lstrip()
        label = projected_label
    if trailing and not re.fullmatch(r'[,;。.；，:：]+', trailing):
        raise ValueError('Labeled display has unsupported trailing prose')
    if decision.get('label') != label:
        raise ValueError('Labeled display changed its immutable text prefix')
    normalization = decision.get('label_normalization')
    if normalization:
        replacement = normalization.get('replacement', '')
        compact = lambda s: s.replace('（', '(').replace('）', ')').replace(' ', '')
        if (normalization.get('original') != label or not normalization.get('rule_id')
                or compact(replacement) != compact(label)):
            raise ValueError('Label normalization must preserve the reviewed marker')
        label = replacement
    rendered_label = render_text(label)
    if decision.get('label_bold'):
        rendered_label = r'\textbf{' + rendered_label + '}'
    indent = decision.get('indent_ccwd', 2)
    if type(indent) not in (int, float) or not 0 <= indent <= 6:
        raise ValueError('Invalid display label indentation')
    # flalign keeps the left structural label and the displayed body on one line;
    # no right equation number is introduced and all text stays selectable.
    return ('\\begin{flalign*}\n\\text{\\hspace{' + f'{indent:g}' + '\\ccwd}' + rendered_label
            + '}&&' + render_math(body) + ((r'\text{' + render_text(trailing) + '}') if trailing else '') + '&&\n\\end{flalign*}')
