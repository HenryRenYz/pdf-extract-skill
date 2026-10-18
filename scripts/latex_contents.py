"""Select native ToC headings from the source contents; never copy its page numbers."""

import re
try:
    from document_structure import chapter_prefix
except ModuleNotFoundError:
    from .document_structure import chapter_prefix


def _key(text):
    return re.sub(r'\s+', '', text)


def build_contents_plan(blocks, numbering):
    source = [b for b in blocks if b.get('chapter_id') == 'front-contents']
    if not source:
        return {'enabled': False, 'source_block_ids': [], 'supplemental_headings': {}}
    if any(b.get('footnotes') or b['type'] in {'figure', 'table'} for b in source):
        raise ValueError('Source contents with notes or artwork need an explicit preservation decision')
    printed, chapter, ignored, unparsed = [], None, [], []
    for block in source:
        for line_number, line in enumerate(block['text'].splitlines(), 1):
            locator = {'source_block_id': block['block_id'], 'line': line_number, 'text': line}
            if not line.strip() or re.fullmatch(r'\s*(?:#+\s*)?(?:目\s*录|contents|table of contents)\s*', line, re.I):
                ignored.append({**locator, 'reason': 'directory_title_or_empty'})
                continue
            # Printed pages identify source rows only; they are never emitted as
            # reading-edition page numbers or copied into the generated .toc.
            match = re.fullmatch(r'\s*(.*?)\s*(?:[…\.．·]{2,}|…|\s{2,}|\t+)\s*'
                                 r'(?:[（(](\d+|[ivxlcdm]+)[）)]|(\d+|[ivxlcdm]+))\s*', line, re.I)
            if not match:
                unparsed.append(locator)
                continue
            title = match[1].strip()
            chap_number, chap_end = chapter_prefix(title)
            section = re.match(r'(\*\s*)?(\d+(?:\.\d+)+)\s+', title)
            if chap_number is not None:
                chapter = 'chapter-' + str(chap_number)
                number, title = str(chap_number), title[chap_end:]
            elif section:
                number, title = section[2], title[section.end():]
            else:
                number = None
            printed.append({'source_block_id': block['block_id'], 'chapter_id': chapter,
                            'source_number': number, 'source_title': title,
                            'optional_marker': bool(section and section[1]),
                            'printed_page': int(match[2] or match[3]) if (match[2] or match[3]).isdigit() else (match[2] or match[3]),
                            'source_line': line_number})
    if unparsed:
        # Never consume an entire contents block while silently dropping rows.
        raise ValueError('Unparsed source contents lines require an explicit review: ' + repr(unparsed))
    if not printed:
        raise ValueError('front-contents has no recognized source directory entries')
    headings = [b for b in blocks if b['type'] == 'heading'
                and not str(b.get('chapter_id', '')).startswith('front-')]
    numbered = {r['source_number']: r for r in numbering['headings'].values()}
    supplemental, matched, unresolved = {}, [], []
    for entry in printed:
        number = entry['source_number']
        if number in numbered:
            record = numbered[number]
            candidates = [b for b in headings if b['block_id'] == record['block_id']]
        elif number:
            # Optional source sections may already use faithful starred heading
            # text. Preserve that body; add a native, page-resolved ToC entry.
            candidates = [b for b in headings if b.get('chapter_id') == entry['chapter_id']
                          and re.match(r'^\*\s*' + re.escape(number) + r'\s+', b['text'])]
        else:
            candidates = [b for b in headings
                          if (b.get('chapter_id') == entry['chapter_id']
                              or (str(b.get('chapter_id', '')).startswith('back-')
                                  and b.get('heading_depth') == 1))
                          and _key(b.get('document_part', {}).get('title', b['text']))
                          == _key(entry['source_title'])]
        if len(candidates) > 1:
            raise ValueError('Ambiguous source ToC heading: ' + entry['source_title'])
        if not candidates:
            unresolved.append(entry)
            continue  # Partial-book exports include actual headings only.
        block = candidates[0]
        ident = block['block_id']
        matched.append({**entry, 'heading_block_id': ident})
        if number not in numbered and not str(block.get('chapter_id', '')).startswith('back-'):
            depth = block.get('heading_depth', 2)
            if not 1 <= depth <= 4:
                raise ValueError('Unsupported supplemental ToC heading depth')
            supplemental[ident] = {
                'command': ('chapter', 'section', 'subsection', 'subsubsection')[depth - 1],
                'optional_number': number, 'optional_marker': entry['optional_marker'],
                'label': 'toc:' + ident, 'source_block_id': entry['source_block_id']}
    target_ids = [x['heading_block_id'] for x in matched]
    if len(target_ids) != len(set(target_ids)):
        raise ValueError('Repeated source ToC entry targets the same heading')
    return {'enabled': True, 'source_block_ids': [b['block_id'] for b in source],
            'source_pages': sorted({b['page'] for b in source}),
            'matched_entries': matched, 'unresolved_source_entries': unresolved,
            'ignored_source_lines': ignored, 'parsed_source_entries': printed,
            'supplemental_headings': supplemental,
            'page_number_policy': 'LaTeX-generated reading-edition pages; original pages remain in source data'}
