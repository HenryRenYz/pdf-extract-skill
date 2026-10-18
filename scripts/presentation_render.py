"""Render explicit reviewed structure without rewriting the immutable text."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path

LIST_PREAMBLE = r"""
\usepackage{enumitem}
\usepackage{needspace,xcolor}
"""

try:
    from semantic_contract import label_marker_identity, project_label_styles
except ImportError:
    from .semantic_contract import label_marker_identity, project_label_styles


def validate_fragment_presentations(block, source_sha256):
    """Authenticate immutable merged fragments against their retained sources."""
    sources = {s.get('block_id'): s for s in block.get('sources', [])}
    for fragment in block.get('source_semantics', []):
        identifier = fragment.get('block_id')
        source = sources.get(identifier)
        if not source or source.get('source_sha256') != source_sha256 or source.get('text_sha256') != fragment.get('text_sha256'):
            raise ValueError('Source semantic fragment identity differs from its retained source')
        for key, decision in fragment.get('fields', {}).items():
            if not key.endswith('_presentation') or not isinstance(decision, dict):
                continue
            review = decision.get('review', {})
            if review.get('source_checked') is not True:
                continue  # candidate snapshots may be retained but not consumed
            for name, expected in [('source_sha256', source_sha256),
                                   ('source_image_sha256', source.get('source_image_sha256')),
                                   ('text_sha256', fragment['text_sha256'])]:
                if not expected or decision.get(name) != expected:
                    raise ValueError('Fragment presentation ' + name + ' mismatch')
            path = Path(review.get('evidence_path', '')).expanduser()
            expected = review.get('evidence_sha256', source.get('source_image_sha256'))
            if not review.get('reviewer') or not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Fragment presentation review evidence is missing or changed')
            if decision.get('source_block_ids') not in (None, [identifier]):
                raise ValueError('Fragment presentation source ownership changed')


def presentation(block, name):
    value = block.get(name)
    if value is None:
        # A merged reading unit retains the first fragment's presentation and
        # its identity under source_semantics. Never rehash it as merged text.
        value = block.get('merged_presentations', {}).get(name)
    if value is not None and value.get('review', {}).get('source_checked') is not True:
        raise ValueError(f"{block.get('block_id', block.get('id'))}: {name} needs source review before rendering")
    return value


def boundary_separator(next_block, next_tex, starts_display):
    decision = presentation(next_block, 'paragraph_presentation')
    if decision:
        boundary = decision['boundary_before']
        if boundary == 'unknown':
            raise ValueError('Unresolved paragraph boundary must be reviewed before export')
        if boundary == 'continue':
            return '\n'
        return '\n\n'
    return '\n' if starts_display(next_tex) else '\n\n'


def paragraph_prefix(block, content):
    decision = presentation(block, 'paragraph_presentation')
    if not decision:
        return content
    if decision['boundary_before'] == 'unknown':
        raise ValueError('Unresolved paragraph boundary must be reviewed before export')
    first = decision['first_line']
    if decision['boundary_before'] == 'continue':
        if first != 'inherit':
            raise ValueError('Continuation must inherit the current paragraph indentation')
        return content
    if first == 'noindent':
        return r'\noindent ' + content
    if first == 'indent':
        return r'\indent ' + content
    return content


def local_heading(block, render):
    decision = presentation(block, 'heading_presentation')
    if not decision or decision['role'] not in {'local_heading', 'algorithm_label'}:
        return None
    size = {'body': r'\normalsize', 'small_heading': r'\small',
            'chapter': r'\Huge', 'section': r'\Large', 'subsection': r'\large'}[decision['font_size']]
    indent = decision.get('indent_ccwd', 0)
    normalization = decision.get('label_normalization')
    if normalization is not None:
        original, replacement = normalization.get('original'), normalization.get('replacement')
        if (not isinstance(original, str) or not original
                or not isinstance(replacement, str) or not replacement
                or not block['text'].startswith(original) or len(original) != len(replacement)
                or label_marker_identity(original) != label_marker_identity(replacement)
                or not normalization.get('rule_id') or not normalization.get('rule_version')):
            raise ValueError('Heading label normalization changes source marker identity or offsets')
        # Equal Unicode lengths preserve every original style-span coordinate.
        # Only the rendering copy changes; source text and hash stay immutable.
        block = {**block, 'text': replacement + block['text'][len(original):]}
    return (r'\par\addvspace{0.6\baselineskip}\noindent'
            + f'\\hspace*{{{indent:g}\\ccwd}}'
            + '{' + size + r'\sffamily\mdseries ' + render(block) + r'\par}'
            + r'\nopagebreak[4]')


def structured_list(block, render, _enum_depth=0):
    decision = presentation(block, 'list_presentation') or {}
    kind = decision.get('kind', block.get('list_kind', 'ordered' if block.get('ordered') else 'unordered'))
    outer = decision.get('outer_indent_ccwd', decision.get('indent_ccwd', 0))
    gap = decision.get('labelsep_ccwd', 0.5)
    label_width = decision.get('labelwidth_ccwd', 0 if kind == 'description' else 1)
    hanging = decision.get('hanging_ccwd', label_width + gap)
    item_indent = decision.get('item_indent_ccwd', 0)
    environment = 'enumerate' if kind == 'ordered' else 'itemize'
    item_options = f'itemindent={item_indent:g}\\ccwd'
    options = (f'leftmargin={outer + hanging:g}\\ccwd,labelsep={gap:g}\\ccwd,'
               f'labelwidth={label_width:g}\\ccwd,' + item_options)
    label_changes = {r['item_index']: r for r in decision.get('label_normalizations', [])}
    prepared = []
    for index, item in enumerate(block['items']):
        item = {'text': item} if isinstance(item, str) else item
        label = item.get('label')
        label_styles = item.get('label_styles', [])
        if index in label_changes:
            replacement = label_changes[index]['replacement']
            label_styles = project_label_styles(label, replacement, label_styles)
            label = replacement
        marker = item.get('marker', '')
        term_item = kind == 'description' or (environment == 'itemize' and 'marker' in item)
        prefix = r'\item'
        # Description labels are text separate from the marker and body; their
        # physical separation must not depend on collapsed source whitespace.
        if term_item:
            if marker in {'●', '•', 'bullet', 'unordered'}:
                prefix += r'[{\labelitemi}]'
            elif marker:
                prefix += '[{' + render({'text': marker, 'styles': []}) + '}]'
            prefix += ' '
            if label:
                prefix += r'\textbf{' + render({'text': label, 'styles': label_styles}) + '}'
                prefix += f'\\hspace{{{decision.get("label_body_gap_ccwd", 1):g}\\ccwd}}'
        elif environment == 'itemize' and label in {'●', '•'} and not label_styles:
            # A source bullet is a structural marker, independent of whether
            # the selected body font covers its literal Unicode glyph.
            prefix += r'[{\labelitemi}] '
        elif label is not None:
            prefix += '[{' + render({'text': label, 'styles': label_styles}) + '}] '
        else:
            prefix += ' '
        if term_item and not marker:
            # A run-in term is not evidence of a bullet in the source.
            prefix = r'\item[] ' + prefix[len(r'\item '):]
        body = render({'text': item['text'], 'styles': item.get('styles', [])})
        prepared.append((item, prefix + body, label, label_styles))

    result = []
    auto_width = 'labelwidth_ccwd' not in decision and 'hanging_ccwd' not in decision
    if auto_width:
        # Measure actual rendered labels in TeX, including reviewed styles and
        # the current font. A fixed one-character box lets multi-digit labels
        # protrude into the margin without producing an overfull-box warning.
        # Borrow native lengths only inside this group; nested lists get their
        # own measurements and restore the outer list's geometry afterwards.
        result = [r'\begingroup', f'\\setlength{{\\labelwidth}}{{{label_width:g}\\ccwd}}']
        enum_names = ('enumi', 'enumii', 'enumiii', 'enumiv')
        for index, (item, _, label, label_styles) in enumerate(prepared):
            term_item = kind == 'description' or (environment == 'itemize' and 'marker' in item)
            if term_item:
                label = item.get('marker', '')
                label_styles = []
            if ((term_item and label in {'●', '•', 'bullet', 'unordered'})
                    or (environment == 'itemize' and label in {'●', '•'} and not label_styles)):
                measured = r'\labelitemi'
            elif label is not None and label != '':
                measured = render({'text': label, 'styles': label_styles})
            elif term_item:
                continue
            elif environment == 'enumerate':
                if _enum_depth >= len(enum_names):
                    raise ValueError('Native ordered lists support at most four enumerate levels')
                counter = enum_names[_enum_depth]
                # Local register assignment preserves the real counter. Use
                # the native label macro so custom templates keep their labels.
                measured = (r'{\csname c@' + counter + r'\endcsname='
                            + str(index + 1) + r'\relax\label' + counter + '}')
            else:
                measured = r'\labelitemi'
            result += [r'\settowidth{\leftmargin}{' + measured + '}',
                       r'\ifdim\leftmargin>\labelwidth\setlength{\labelwidth}{\leftmargin}\fi']
        result += [r'\edef\BookListMeasuredLabelWidth{\the\labelwidth}']
        measured_gap = 0 if kind == 'description' and not any(item.get('marker') for item, *_ in prepared) else gap
        options = (r'leftmargin=\dimexpr' + f'{outer + measured_gap:g}\\ccwd+'
                   + r'\BookListMeasuredLabelWidth\relax,'
                   + f'labelsep={measured_gap:g}\\ccwd,labelwidth=\\BookListMeasuredLabelWidth,'
                   + item_options)
    result.append(f'\\begin{{{environment}}}[{options}]')
    for item, content, _, _ in prepared:
        result.append(content)
        if item.get('children'):
            child = {**block, 'items': item['children'], 'list_presentation': None,
                     'merged_presentations': {}, 'list_kind': item.get('child_kind', kind)}
            result.append(structured_list(child, render, _enum_depth + (environment == 'enumerate')))
    result.append(f'\\end{{{environment}}}')
    if auto_width:
        result.append(r'\endgroup')
    return '\n'.join(result)
