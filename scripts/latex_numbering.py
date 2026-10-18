#!/usr/bin/env python3
"""Source-number index and native LaTeX numbering plans, without changing OCR.

Offsets use Python Unicode indices in the original semantic block text. A caller
must consume bindings while rendering that source, never replace strings in
already rendered TeX. Only explicitly numbered source objects acquire counters.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
try:
    from document_parts import unnumbered_part
    from document_structure import chapter_prefix
except ModuleNotFoundError:
    from .document_parts import unnumbered_part
    from .document_structure import chapter_prefix


class NumberingError(ValueError):
    pass


COUNTERS = ('chapter', 'section', 'subsection', 'subsubsection', 'paragraph', 'subparagraph')
_TAG = re.compile(r'\\tag(\*)?\s*\{')
_NUMBER = r'\d+(?:\.\d+)+'
_PRIME = r"(?:['′]|\^\s*\{?\s*\\prime\s*\}?)"


def block_id(block: dict) -> str:
    value = block.get('block_id') or block.get('id')
    if not isinstance(value, str) or not value:
        raise NumberingError('numbered source object lacks a stable block ID')
    return value


def safe_label(prefix: str, identifier: str, ordinal: int = 1) -> str:
    """Readable labels remain stable when unrelated objects are added."""
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+', identifier):
        identifier = 'id-' + sha256(identifier.encode()).hexdigest()[:20]
    return f'{prefix}:{identifier}' + (f'-d{ordinal}' if ordinal > 1 else '')


def _outer_text_interval(text: str) -> tuple[int, int]:
    a, b = 0, len(text)
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    prefix = re.match(r'#{1,6}\s+', text[a:b])
    if prefix:
        a += prefix.end()
    # A complete Markdown emphasis wrapper is semantic styling, not a title.
    for marker in ('**', '__'):
        if text[a:b].startswith(marker) and text[a:b].endswith(marker):
            a += len(marker)
            b -= len(marker)
            break
    return a, b


def heading_record(block: dict, family='book') -> dict | None:
    if block.get('type') != 'heading' or unnumbered_part(block.get('chapter_id', '')):
        return None
    text = block['text']
    a, b = _outer_text_interval(text)
    depth = block.get('heading_depth', 2)
    counters = COUNTERS if family == 'book' else COUNTERS[1:]
    if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= len(counters):
        raise NumberingError(f'{block_id(block)}: invalid heading depth')
    if family == 'article':
        match = re.match(r'(\d+(?:\.\d+)*)(?:\.?\s+|(?=[\u3400-\u9fff]))', text[a:b])
        parts = [int(v) for v in match.group(1).split('.')] if match else []
        prefix_end = match.end() if match else 0
    elif depth == 1:
        number, prefix_end = chapter_prefix(text[a:b])
        parts = [number] if number is not None else []
        match = number is not None
    else:
        match = re.match(r'(\d+(?:\.\d+)+)(?:\s+|(?=[\u3400-\u9fff]))', text[a:b])
        parts = [int(v) for v in match.group(1).split('.')] if match else []
        prefix_end = match.end() if match else 0
    if not match:
        return None  # Source end-of-chapter headings are intentionally unnumbered.
    if len(parts) != depth or parts[0] < 0 or any(v < 1 for v in parts[1:]):
        raise NumberingError(f'{block_id(block)}: printed heading number does not match depth')
    declared_chapter = re.fullmatch(r'chapter-(\d+)', str(block.get('chapter_id', '')))
    if family == 'book' and declared_chapter and int(declared_chapter.group(1)) != parts[0]:
        raise NumberingError(f'{block_id(block)}: printed heading contradicts chapter identity')
    return {'block_id': block_id(block), 'kind': 'chapter' if depth == 1 and family == 'book' else 'section',
            'depth': depth, 'command': counters[depth - 1], 'number_parts': parts,
            'source_number': '.'.join(map(str, parts)), 'label': safe_label('sec', block_id(block)),
            'title_start': a + prefix_end, 'title_end': b, 'text_sha256': sha256(text.encode()).hexdigest()}


def caption_record(block: dict) -> dict | None:
    if block.get('type') != 'caption':
        return None
    text = block['text']
    a, b = _outer_text_interval(text)
    match = re.match(r'(图|表|Figure|Fig\.?|Table)\s*(\d+)\s*[-－–—.]\s*(\d+)\s*[:：.]?\s*', text[a:b], re.I)
    if not match:
        return None
    chapter, local = int(match.group(2)), int(match.group(3))
    if chapter < 0 or local < 1:
        raise NumberingError(f'{block_id(block)}: invalid caption number')
    kind = 'figure' if match.group(1).lower() in {'图', 'figure', 'fig', 'fig.'} else 'table'
    return {'block_id': block_id(block), 'kind': kind, 'counter': kind,
            'number_parts': [chapter, local], 'source_number': f'{chapter}-{local}',
            'label': safe_label('fig' if kind == 'figure' else 'tab', block_id(block)),
            'title_start': a + match.end(), 'title_end': b,
            'text_sha256': sha256(text.encode()).hexdigest()}


def slice_title(block: dict, record: dict) -> dict:
    """Return a render copy, clipping styles rather than changing source JSON."""
    text = block['text']
    if record['text_sha256'] != sha256(text.encode()).hexdigest():
        raise NumberingError('title numbering plan is stale for source text')
    a, b = record['title_start'], record['title_end']
    styles = []
    for style in block.get('styles', []):
        left, right = max(a, style['start']), min(b, style['end'])
        if left < right:
            styles.append({**style, 'start': left - a, 'end': right - a})
    return {**block, 'text': text[a:b], 'styles': styles, '_source_offset_base': a}


def _balanced_close(text: str, start: int) -> int:
    level, i = 1, start
    while i < len(text):
        if text[i] == '\\':
            # Escaped braces do not alter grouping; command braces still do.
            if i + 1 < len(text) and text[i + 1] in '{}':
                i += 2
                continue
        elif text[i] == '{':
            level += 1
        elif text[i] == '}':
            level -= 1
            if level == 0:
                return i
        i += 1
    raise NumberingError('unclosed equation tag argument')


def parse_equation_tag(content: str) -> dict | None:
    matches = list(_TAG.finditer(content))
    if not matches:
        return None
    if len(matches) != 1:
        raise NumberingError('a display contains multiple equation tags; split or review its source')
    match = matches[0]
    close = _balanced_close(content, match.end())
    argument = content[match.end():close]
    compact = re.sub(r'\s+', '', argument)
    # These wrappers occur in canonical OCR and the old export, respectively.
    compact = re.sub(r'\\text\{([′\'])\}', r'\1', compact)
    if compact.startswith(r'\ensuremath{') and compact.endswith('}'):
        compact = compact[len(r'\ensuremath{'):-1]
    numeric_argument = compact[1:-1] if compact.startswith('(') and compact.endswith(')') else compact
    normal = re.fullmatch(r'(' + _NUMBER + r')', numeric_argument)
    outside = re.fullmatch(r'\((' + _NUMBER + r')\)' + _PRIME, compact)
    inside = re.fullmatch(r'\(?(' + _NUMBER + r')' + _PRIME + r'\)?', compact)
    parsed = normal or outside or inside
    result = {'tag_start': match.start(), 'tag_end': close + 1,
              'source_argument': argument, 'source_tag_starred': bool(match.group(1)),
              'math_without_tag': content[:match.start()] + content[close + 1:]}
    if not parsed or compact.count('(') != compact.count(')'):
        return {**result, 'supported': False, 'reason': 'nonstandard_source_tag_needs_review'}
    number = parsed.group(1)
    parts = [int(v) for v in number.split('.')]
    if len(parts) != 2 or min(parts) < 1:
        return {**result, 'supported': False, 'reason': 'non_chapter_equation_number_needs_review'}
    prime = not bool(normal)
    return {**result, 'supported': True, 'source_number': number + ("'" if prime else ''),
            'base_number': number, 'number_parts': parts, 'prime': prime,
            'prime_position': 'outside_parentheses' if outside else 'inside_parentheses' if prime else None}


def numbered_system_segments(block: dict) -> list[dict]:
    """Reviewed literal array row numbers become native equation targets."""
    contract = block.get('numbered_system', {})
    text = block.get('text', '')
    review = contract.get('review', {})
    if contract.get('schema_version') != 1 or block.get('type') != 'formula':
        raise NumberingError('numbered_system requires a schema 1 formula contract')
    for key in ('source_sha256', 'source_image_sha256'):
        if contract.get(key) != block.get(key):
            raise NumberingError('numbered_system source/image hash changed')
    if contract.get('text_sha256') != sha256(text.encode()).hexdigest():
        raise NumberingError('numbered_system original text hash changed')
    if review.get('source_checked') is not True or not review.get('reviewer') or not review.get('evidence_path'):
        raise NumberingError('numbered_system needs actual source review')
    evidence = Path(review['evidence_path'])
    if not evidence.is_file() or sha256(evidence.read_bytes()).hexdigest() != review.get('evidence_sha256'):
        raise NumberingError('numbered_system source evidence hash changed')
    source_ids = [source['block_id'] for source in block.get('sources', [])] or [block_id(block)]
    if contract.get('source_block_ids') != source_ids:
        raise NumberingError('numbered_system source ownership changed')
    match = re.fullmatch(r'\s*\\\[\s*\\left\\\{\s*\\begin\{array\}\{[lcr]+\}(.*?)'
                         r'\\end\{array\}\s*\\right\.\s*\\\]\s*', text, re.S)
    if not match:
        raise NumberingError('numbered_system supports a complete reviewed left-braced simple array only')
    body = match.group(1)
    chunks, rows = body.split(r'\\'), contract.get('rows')
    if not isinstance(rows, list) or len(rows) < 2 or len(rows) != len(chunks):
        raise NumberingError('numbered_system rows do not cover source array')
    result, cursor, prior = [], match.start(1), None
    for ordinal, (chunk, row) in enumerate(zip(chunks, rows), 1):
        item = re.fullmatch(r'\s*(.*?)\s*&\s*\(\s*(\d+\.\d+)\s*\)\s*', chunk, re.S)
        if not item or '&' in item.group(1) or row.get('index') != ordinal - 1:
            raise NumberingError('numbered_system row contains unsupported alignment/source numbering')
        math = item.group(1).strip()
        number = item.group(2)
        if row.get('math') != math or row.get('source_number') != number:
            raise NumberingError('numbered_system row mathematical content or source number changed')
        parts = [int(n) for n in number.split('.')]
        if prior and parts != [prior[0], prior[1] + 1]:
            raise NumberingError('numbered_system native numcases requires consecutive same-chapter source numbers')
        prior = parts
        start = cursor + item.start(1) + len(item.group(1)) - len(item.group(1).lstrip())
        end = start + len(math)
        tag = parse_equation_tag(math + r'\tag{' + number + '}')
        if not tag or not tag.get('supported') or tag.get('prime'):
            raise NumberingError('numbered_system requires ordinary source equation numbers')
        result.append({'start': start, 'end': end, 'content_start': start, 'content_end': end,
                       'math': math, 'ordinal': ordinal, 'block_id': block_id(block),
                       'tag': {**tag, 'source_format': 'reviewed_literal_array_row_number'},
                       'numbered_system': True})
        cursor += len(chunk) + 2
    return result


def display_segments(block: dict) -> list[dict]:
    """Find each display, including multiple displays in one semantic block."""
    if block.get('numbered_system'):
        return numbered_system_segments(block)
    text = block.get('text', '')
    segments, i = [], 0
    while i < len(text):
        opening = re.search(r'(?<!\\)(?:\$\$|\\\[)', text[i:])
        if not opening:
            break
        a = i + opening.start()
        token = opening.group()
        content_start = a + len(token)
        closing = '$$' if token == '$$' else r'\]'
        b = text.find(closing, content_start)
        while b >= 0 and b > 0 and text[b - 1] == '\\':
            b = text.find(closing, b + len(closing))
        if b < 0:
            raise NumberingError(f'{block_id(block)}: unclosed display delimiter')
        segments.append({'start': a, 'end': b + len(closing), 'content_start': content_start,
                         'content_end': b, 'math': text[content_start:b]})
        i = b + len(closing)
    if (not segments and block.get('type') == 'formula'
            and not re.search(r'(?<!\\)(?:\$|\\\()', text)):
        segments = [{'start': 0, 'end': len(text), 'content_start': 0, 'content_end': len(text), 'math': text}]
    for ordinal, segment in enumerate(segments, 1):
        segment.update(ordinal=ordinal, block_id=block_id(block), tag=parse_equation_tag(segment['math']))
    return segments


def _target_key(kind: str, number: str) -> str:
    return f'{kind}:{number}'


def reference_latex(target: dict) -> str:
    label = target['label']
    if target['kind'] == 'equation':
        if target.get('prime'):
            # AMS eqref would add a second pair around an outside-parenthesis tag.
            base = target['base_label']
            if target['prime_position'] == 'outside_parentheses':
                printed = r'(\ref*{' + base + r'})\ensuremath{^{\prime}}'
            else:
                printed = r'(\ref*{' + base + r'}\ensuremath{^{\prime}})'
            return r'\hyperref[' + label + ']{' + printed + '}'
        return r'\eqref{' + label + '}'
    if target['kind'] == 'chapter':
        return r'\mbox{第\ref{' + label + '}章}'
    if target['kind'] == 'figure':
        return r'\mbox{图\ref{' + label + '}}'
    if target['kind'] == 'table':
        return r'\mbox{表\ref{' + label + '}}'
    return r'\mbox{\ref{' + label + '}}'


def _math_intervals(text: str) -> list[tuple[int, int]]:
    """Mask math, HTML tags and code; refs inside formulas are never guessed."""
    result = []
    token_re = re.compile(r'(?<!\\)(\$\$|\$|\\\[|\\\(|`+)')
    i = 0
    while i < len(text):
        match = token_re.search(text, i)
        if not match:
            break
        token = match.group()
        end_token = {'\\[': '\\]', '\\(': '\\)'}.get(token, token)
        j = text.find(end_token, match.end())
        if j < 0:
            # The canonical inline renderer will reject an unmatched math delimiter.
            result.append((match.start(), len(text)))
            break
        result.append((match.start(), j + len(end_token)))
        i = j + len(end_token)
    result += [(m.start(), m.end()) for m in re.finditer(r'<[^>]*>', text)]
    return result


def reference_bindings(text: str, targets: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Bind explicit prose citations only; plain (1.2) or decimal values stay raw.

    A source formula ID in parentheses requires nearby textbook citation words
    or the Chinese equation marker 式 immediately after it. Standalone math
    citations like $(2.41)^\\prime$ are accepted only when the entire math token
    is a numbered citation followed by that marker.
    """
    bindings, unresolved = [], []
    masked = _math_intervals(text)

    def emit(kind, number, start, end, evidence):
        key = _target_key(kind, number)
        target = targets.get(key)
        item = {'start': start, 'end': end, 'source_text': text[start:end], 'kind': kind,
                'source_number': number, 'evidence': evidence}
        if target:
            bindings.append({**item, 'label': target['label'], 'latex': reference_latex(target)})
        else:
            unresolved.append({**item, 'reason': 'target_outside_processed_scope_or_unregistered'})

    def outside(a, b):
        return not any(a < y and b > x for x, y in masked)

    for match in re.finditer(r'(图|表)\s*(\d+)\s*[-－–—.]\s*(\d+)', text):
        if outside(match.start(), match.end()):
            emit('figure' if match.group(1) == '图' else 'table',
                 f'{int(match.group(2))}-{int(match.group(3))}', match.start(), match.end(), 'explicit_figure_table_prefix')
    for match in re.finditer(r'第\s*(\d+)\s*章', text):
        if outside(match.start(), match.end()):
            emit('chapter', str(int(match.group(1))), match.start(), match.end(), 'explicit_chapter_prefix')
    for match in re.finditer(r'(?<![\d.])(\d+(?:\.\d+){1,5})\s*节', text):
        if outside(match.start(), match.end()):
            # Preserve the source suffix; the binding replaces its numeric part.
            emit('section', match.group(1), match.start(1), match.end(1), 'explicit_section_suffix')
    for match in re.finditer(r'[（(]\s*(' + _NUMBER + r')\s*[）)]\s*([′\'])?', text):
        a, b = match.span()
        # Whitespace belongs to the following prose, not the reference token.
        if not match.group(2):
            b = a + len(match.group().rstrip())
        if not outside(a, b):
            continue
        after, before = text[b:b + 8].lstrip(), text[max(0, a - 14):a]
        strong = bool(re.match(r'(?:式|方程)', after)) or bool(re.search(r'(?:式|由|根据|按|用|将|利用|表达式|方程组|方程|多项式|条件|公式|算法|迭代法|满足|即|及|和|与|类似|得到|表示|余项|表达式)\s*$', before))
        if not strong:
            continue
        emit('equation', match.group(1) + ("'" if match.group(2) else ''), a, b, 'explicit_equation_citation_context')
    # An entire inline-math citation followed by the Chinese equation marker 式
    # is safe to replace as one token.
    for a, b in masked:
        token = text[a:b]
        if not token.startswith('$') or token.startswith('$$') or not re.match(r'\s*式', text[b:]):
            continue
        inner = token[1:-1].strip()
        parsed = parse_equation_tag(r'\tag*{' + inner + '}')
        if parsed and parsed.get('supported'):
            emit('equation', parsed['source_number'], a, b, 'whole_inline_math_equation_citation_followed_by_式')
    bindings.sort(key=lambda x: (x['start'], x['end']))
    if any(left['end'] > right['start'] for left, right in zip(bindings, bindings[1:])):
        raise NumberingError('overlapping generated reference bindings')
    return bindings, unresolved


def _validated_overrides(blocks: list[dict], records: list[dict] | None) -> dict[tuple[str, int], dict]:
    indexed, result = {block_id(b): b for b in blocks}, {}
    for record in records or []:
        ident = record.get('block_id')
        block = indexed.get(ident)
        ordinal = record.get('display_ordinal', 1)
        review = record.get('review', {})
        if not block or not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
            raise NumberingError('numbering override targets an unknown block/display')
        if review.get('source_checked') is not True or not review.get('reviewer') or not review.get('evidence'):
            raise NumberingError(f'{ident}: numbering override requires explicit source review and evidence')
        for field in ('source_sha256', 'source_image_sha256'):
            if not record.get(field) or record[field] != block.get(field):
                raise NumberingError(f'{ident}: numbering override has stale {field}')
        if record.get('text_sha256') != sha256(block['text'].encode()).hexdigest():
            raise NumberingError(f'{ident}: numbering override has stale text hash')
        if record.get('prime_position') not in {'outside_parentheses', 'inside_parentheses'}:
            raise NumberingError(f'{ident}: only a reviewed prime-position override is supported')
        key = (ident, ordinal)
        if key in result:
            raise NumberingError(f'{ident}: duplicate numbering override')
        result[key] = record
    return result


def build_numbering_plan(blocks: list[dict], numbering_overrides: list[dict] | None = None, *, family='book') -> dict:
    """Plan standard counters and source-checked citation targets, keeping inputs immutable."""
    headings, captions, displays, targets, warnings, discontinuities = {}, {}, {}, {}, [], []
    chapter_contexts = {}
    overrides = _validated_overrides(blocks, numbering_overrides)
    used_overrides = set()
    counters = COUNTERS if family == 'book' else COUNTERS[1:]
    heading_counters = [0] * len(counters)
    object_counters = {'equation': 0, 'figure': 0, 'table': 0}
    current_chapter = 0

    def register(target):
        key = _target_key(target['kind'], target['source_number'])
        if key in targets:
            raise NumberingError(f'ambiguous duplicate source target {key}: {targets[key]["block_id"]}, {target["block_id"]}')
        targets[key] = target

    def prepare_counter(record, kind):
        chapter, local = record['number_parts']
        if chapter != current_chapter:
            raise NumberingError(f'{record["block_id"]}: numbered {kind} appears outside its native chapter')
        expected = object_counters[kind] + 1
        record['counter_precommands'] = [] if local == expected else [rf'\setcounter{{{kind}}}{{{local - 1}}}']
        if local != expected:
            discontinuities.append({'block_id': record['block_id'], 'kind': kind, 'expected': expected, 'source': local})
        object_counters[kind] = local

    for block in blocks:
        ident = block_id(block)
        heading = heading_record(block, family)
        declared = re.fullmatch(r'chapter-(\d+)', str(block.get('chapter_id', '')))
        if family == 'book' and declared and int(declared[1]) != current_chapter and not (heading and heading['depth'] == 1):
            # Reviewed excerpt context may precede any real body heading. Set
            # counters without inventing a chapter title or a ToC entry.
            current_chapter = int(declared[1])
            heading_counters = [current_chapter] + [0] * (len(COUNTERS) - 1)
            object_counters = dict.fromkeys(object_counters, 0)
            chapter_contexts[ident] = [rf'\setcounter{{chapter}}{{{current_chapter}}}'] + [
                rf'\setcounter{{{name}}}{{0}}' for name in (*COUNTERS[1:], 'equation','figure','table')] + [r'\markboth{}{}']
        if heading:
            parts, depth = heading['number_parts'], heading['depth']
            commands = []
            for index, value in enumerate(parts):
                desired = value - 1 if index == depth - 1 else value
                actual = heading_counters[index]
                if actual != desired:
                    commands.append(rf'\setcounter{{{counters[index]}}}{{{desired}}}')
            heading['counter_precommands'] = commands
            if commands:
                discontinuities.append({'block_id': ident, 'kind': heading['kind'], 'source': heading['source_number'], 'counter_precommands': commands})
            heading_counters[:depth] = parts
            heading_counters[depth:] = [0] * (len(counters) - depth)
            if depth == 1:
                current_chapter = parts[0]
                object_counters = dict.fromkeys(object_counters, 0)
            headings[ident] = heading
            register(heading)
        caption = caption_record(block)
        if caption and not unnumbered_part(block.get('chapter_id', '')):
            prepare_counter(caption, caption['kind'])
            captions[ident] = caption
            register(caption)
        if unnumbered_part(block.get('chapter_id', '')):
            continue
        segments = display_segments(block)
        if not segments:
            continue
        for segment in segments:
            segment.update(environment='equation*', counter_precommands=[], label=None)
            tag = segment['tag']
            if not tag:
                continue
            if not tag['supported']:
                warnings.append({'block_id': ident, 'display': segment['ordinal'], **tag})
                continue
            override_key = (ident, segment['ordinal'])
            override = overrides.get(override_key)
            if override:
                if not tag['prime'] or override.get('source_number') != tag['source_number']:
                    raise NumberingError(f'{ident}: reviewed override does not match a source prime equation')
                tag = {**tag, 'source_tag_prime_position': tag['prime_position'],
                       'prime_position': override['prime_position'], 'numbering_override': override}
                segment['tag'] = tag
                used_overrides.add(override_key)
            target = {**tag, 'kind': 'equation', 'block_id': ident,
                      'label': safe_label('eq', ident, segment['ordinal'])}
            segment.update(label=target['label'], source_number=tag['source_number'],
                           math_without_tag=tag['math_without_tag'])
            if tag['prime']:
                # Prime variants do not advance the ordinary equation counter.
                segment['prime'] = True
            else:
                prepare_counter(target, 'equation')
                segment.update(environment='equation', counter_precommands=target['counter_precommands'])
            register(target)
        displays[ident] = segments
    if used_overrides != set(overrides):
        raise NumberingError('numbering override did not match a supported prime display')
    # Resolve exceptions after all base formulas have been registered (forward refs work).
    for segments in displays.values():
        for segment in segments:
            tag = segment['tag']
            if not tag or not tag.get('supported') or not tag.get('prime'):
                continue
            target = targets[_target_key('equation', tag['source_number'])]
            base = targets.get(_target_key('equation', tag['base_number']))
            if not base:
                raise NumberingError(f'{segment["block_id"]}: prime equation has no base formula in processed scope')
            target['base_label'] = base['label']
            if tag['prime_position'] == 'outside_parentheses':
                segment['exception_tag_latex'] = r'\tag*{(\ref{' + base['label'] + r'})\ensuremath{^{\prime}}}'
            else:
                segment['exception_tag_latex'] = r'\tag{\ref{' + base['label'] + r'}\ensuremath{^{\prime}}}'
    references, unresolved = {}, []
    for block in blocks:
        # Numbered object titles are not citations. Original full-book contents
        # are retained, without claiming destinations for excluded chapters.
        if block.get('type') in {'heading', 'caption', 'formula', 'figure', 'table', 'header', 'footer'}:
            continue
        found, missing = reference_bindings(block.get('text', ''), targets)
        if found:
            references[block_id(block)] = found
        unresolved += [{'block_id': block_id(block), **item} for item in missing]
    return {'schema_version': 'native-latex-numbering-v1', 'headings': headings,
            'chapter_contexts':chapter_contexts,
            'captions': captions, 'displays': displays, 'targets': targets,
            'references': references, 'unresolved_references': unresolved,
            'warnings': warnings, 'source_counter_discontinuities': discontinuities,
            'numbering_overrides': list(overrides.values()),
            'counts': {'numbered_headings': len(headings), 'numbered_captions': len(captions),
                       'numbered_equations': sum(t['kind'] == 'equation' for t in targets.values()),
                       'prime_equation_exceptions': sum(t.get('prime', False) for t in targets.values()),
                       'reference_bindings': sum(map(len, references.values())),
                       'unresolved_references': len(unresolved)}}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blocks', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    plan = build_numbering_plan([json.loads(line) for line in args.blocks.read_text().splitlines() if line.strip()])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'counts': plan['counts'], 'discontinuities': plan['source_counter_discontinuities'],
                      'warnings': plan['warnings']}, ensure_ascii=False, indent=2))
