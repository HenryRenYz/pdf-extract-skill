#!/usr/bin/env python3
"""Deterministic Markdown and retrieval chunks from the shared semantic JSONL."""
import argparse
import hashlib
from html import escape
import json
import re
from pathlib import Path
import shutil
from asset_contract import resolve_asset
from source_identity import file_hash
from table_data import table_rows


def chunk_blocks(blocks, max_chars):
    """Pack whole semantic units; the budget is characters, never token counts."""
    chunks, pending, size, chapter = [], [], 0, None
    def unit_text(block):
        notes = [child['text'] for note in block.get('footnotes', []) for child in note['blocks']]
        return '\n\n'.join([block['text'], *notes])
    def flush():
        if not pending:
            return
        text = '\n\n'.join(unit_text(b) for b in pending)
        ids = [b.get('block_id', b.get('id')) for b in pending]
        chunks.append({'id': 'chunk-' + hashlib.sha256('\0'.join(ids).encode()).hexdigest()[:20],
                       'chapter_id': chapter, 'block_ids': ids, 'text': text,
                       'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                       'styles_by_block': {b.get('block_id', b.get('id')): b.get('styles', []) for b in pending},
                       'sources': [s for b in pending for s in b.get('sources', [b])],
                       'footnotes': [note for b in pending for note in b.get('footnotes', [])],
                       'oversize': len(text) > max_chars})
    for block in blocks:
        if block['type'] in {'header', 'footer', 'blank'}:
            continue
        current = block.get('chapter_id')
        length = len(unit_text(block))
        if pending and (current != chapter or size + length + 2 > max_chars):
            flush(); pending, size = [], 0
        chapter = current
        pending.append(block); size += length + (2 if len(pending) > 1 else 0)
    flush()
    return chunks


def table_html(block):
    """A portable data view; source ruling/fonts remain independent metadata."""
    rows = table_rows(block)
    if not rows:
        raise ValueError('Empty table data')
    output = ['<table>']
    for row in rows:
        output.append('<tr>')
        for cell in row:
            tag = 'th' if cell.header else 'td'
            attributes = ''.join(f' {name}="{value}"' for name, value in (
                ('rowspan', cell.rowspan), ('colspan', cell.colspan)) if value > 1)
            content = escape(cell.text)
            # Whole-cell explicit candidates can be displayed without
            # transporting offsets through escaped HTML. Partial styles remain
            # lossless in blocks.jsonl, as do all other semantic styles.
            for style in cell.styles or []:
                if style['start'] == 0 and style['end'] == len(cell.text):
                    if style['kind'] in {'bold', 'bold_italic'}:
                        content = '<strong>'+content+'</strong>'
                    if style['kind'] in {'italic', 'bold_italic'}:
                        content = '<em>'+content+'</em>'
            output.append(f'<{tag}{attributes}>{content}</{tag}>')
        output.append('</tr>')
    output.append('</table>')
    return '\n'.join(output)


def markdown(block, rendering_issues=None):
    kind, text = block['type'], block['text']
    if kind in {'header', 'footer', 'blank'}:
        return ''
    if kind == 'heading':
        return '#' * min(6, max(1, block.get('heading_depth', 2))) + ' ' + text.lstrip('#').strip()
    if kind == 'code':
        fence = '`' * max(3, max((len(t) for t in re.findall(r'`+', text)), default=0) + 1)
        return fence + '\n' + text + '\n' + fence
    if kind == 'formula' and not text.lstrip().startswith(('$$', '\\[', '$', '\\(')):
        return '$$\n' + text + '\n$$'
    if kind == 'table':
        try:
            return table_html(block)
        except ValueError as exc:
            if rendering_issues is not None:
                rendering_issues.append({'block_id': block.get('block_id', block.get('id')),
                                         'reason': str(exc), 'action': 'original table text preserved; needs_review'})
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blocks', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--figures', type=Path, help='Optional registry of preserved or reconstructed assets')
    parser.add_argument('--max-chars', type=int, default=6000)
    parser.add_argument('--allow-unresolved-images', action='store_true')
    args = parser.parse_args()
    if args.output.exists() or args.max_chars < 1:
        parser.error('Use a fresh output directory and a positive character budget')
    blocks = [json.loads(line) for line in args.blocks.read_text().splitlines() if line.strip()]
    ids = [b.get('block_id', b.get('id')) for b in blocks]
    if not blocks or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        parser.error('Semantic blocks require unique stable IDs')
    hashes = {b.get('source_sha256') for b in blocks}
    if len(hashes) != 1 or None in hashes:
        parser.error('Semantic blocks must share a document source hash')
    for b in blocks:
        if b.get('text_sha256') != hashlib.sha256(b['text'].encode()).hexdigest():
            parser.error('Semantic text hash changed: ' + b.get('block_id', b.get('id')))
    registry = {}
    if args.figures:
        data = json.loads(args.figures.read_text())
        if not isinstance(data, dict) or data.get('source_sha256') not in hashes:
            parser.error('Figure registry source differs')
        registry = {r.get('figure_id', r.get('id')): r for r in data['figures']}
    assets, missing = {}, []
    for block in blocks:
        if block['type'] != 'figure':
            continue
        identifier = block.get('figure_id', block.get('block_id', block.get('id')))
        entry = registry.get(identifier, {})
        reference = entry.get('final_assets', {}).get('latex') or entry.get('final_asset')
        if reference and args.figures:
            assets[identifier] = resolve_asset(reference, args.figures.parent)
        else:
            missing.append(block.get('block_id', block.get('id')))
    if missing and not args.allow_unresolved_images:
        parser.error('Figures need selected source assets (or explicit --allow-unresolved-images): ' + repr(missing))
    args.output.mkdir(parents=True)
    parts, rendering_issues = [], []
    for block in blocks:
        rendered = markdown(block, rendering_issues)
        identifier = block.get('figure_id', block.get('block_id', block.get('id')))
        if block['type'] == 'figure' and identifier in assets:
            source = assets[identifier]
            name = hashlib.sha256(identifier.encode()).hexdigest()[:16] + source.suffix.lower()
            target = args.output / 'images' / name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
            rendered += '\n\n![](images/' + name + ')'
        if rendered:
            parts.append(rendered)
        for note in block.get('footnotes', []):
            parts.append('\n'.join(markdown(child, rendering_issues) for child in note['blocks']))
    (args.output / 'document.md').write_text('\n\n'.join(parts) + '\n')
    shutil.copyfile(args.blocks, args.output / 'blocks.jsonl')
    chunks = chunk_blocks(blocks, args.max_chars)
    (args.output / 'chunks.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in chunks))
    report = {'source_sha256': next(iter(hashes)), 'input_blocks_sha256': file_hash(args.blocks),
              'chunks': len(chunks), 'chunk_budget_unit': 'Unicode characters',
              'unresolved_image_blocks': missing, 'rendering_issues': rendering_issues,
              'status': 'needs_review' if missing or rendering_issues else 'exported',
              'limits': 'Markdown does not reproduce exact source fonts/layout. All semantic styles and provenance remain in blocks.jsonl. Content accuracy is not certified.'}
    (args.output / 'export-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
