"""Reviewed back matter roles and native column layout; no text rewriting."""
from hashlib import sha256
import json
from pathlib import Path
import re


def normalized_title(value):
    return re.sub(r'\s+', '', re.sub(r'^(?:#+\s*)|[*_`]', '', value))


def unnumbered_part(chapter_id):
    return str(chapter_id).startswith(('front-', 'back-'))


def part_title_matches(text, part):
    return normalized_title(text) in {normalized_title(t) for t in part.get('title_variants', [part['title']])}


def load_document_parts(path, source_hash, page_meta=None):
    path = Path(path).expanduser().resolve(strict=True)
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get('schema_version') != 1 or data.get('source_sha256') != source_hash:
        raise ValueError('Document parts source/schema mismatch')
    parts, by_page, seen = [], {}, set()
    for item in data.get('parts', []):
        ident, role, pages = item.get('part_id'), item.get('role'), item.get('pages')
        review = item.get('review', {})
        columns = item.get('columns', 1)
        if (not isinstance(ident, str) or not re.fullmatch(r'back-[A-Za-z0-9_-]+', ident)
                or ident in seen or role not in ('answers', 'references', 'index', 'appendix')
                or not isinstance(item.get('title'), str) or not item['title'].strip()
                or not isinstance(pages, list) or not pages
                or any(type(n) is not int or n < 1 for n in pages)
                or pages != list(range(pages[0], pages[-1] + 1))
                or any(n in by_page for n in pages)
                or type(columns) is not int or columns not in (1, 2)
                or (columns == 2 and role != 'index')):
            raise ValueError('Invalid/overlapping document part or unsupported column layout')
        if review.get('source_checked') is not True or not review.get('reviewer'):
            raise ValueError('Document part requires actual source review')
        evidence = review.get('evidence_pages', [])
        if not isinstance(evidence, list) or [e.get('page') for e in evidence] != pages:
            raise ValueError('Document part review must cover each owned source page in order')
        for entry in evidence:
            image = Path(entry.get('evidence_path', '')).expanduser()
            if not image.is_file() or sha256(image.read_bytes()).hexdigest() != entry.get('evidence_sha256'):
                raise ValueError('Document part source image missing/changed')
            if page_meta is not None and (entry['page'] not in page_meta
                    or page_meta[entry['page']]['original']['sha256'] != entry['evidence_sha256']):
                raise ValueError('Document part evidence does not match prepared original page')
        compact = {k: item[k] for k in ('part_id', 'role', 'pages', 'title')}
        variants = item.get('title_variants', [item['title']])
        if (not isinstance(variants, list) or not variants
                or any(not isinstance(t, str) or not t.strip() for t in variants)
                or normalized_title(item['title']) not in {normalized_title(t) for t in variants}):
            raise ValueError('Reviewed document part title variants invalid')
        compact['title_variants'] = variants
        compact.update(columns=columns, source_sha256=source_hash,
                       contract_path=str(path), contract_sha256=sha256(raw).hexdigest())
        parts.append(compact)
        seen.add(ident)
        by_page.update({n: compact for n in pages})
    if not parts:
        raise ValueError('Document parts contract is empty')
    return parts, by_page


def build_part_plan(blocks, source_hash):
    """Reverify pinned evidence, roles, complete ownership and heading identity."""
    plans, contracts = {}, {}
    for block in blocks:
        item = block.get('document_part')
        if not item:
            continue
        path = item.get('contract_path')
        if not path:
            raise ValueError('Document part metadata lacks pinned source contract')
        if path not in contracts:
            p = Path(path)
            if not p.is_file() or sha256(p.read_bytes()).hexdigest() != item.get('contract_sha256'):
                raise ValueError('Document parts contract changed since assembly')
            contracts[path] = {'by_page': load_document_parts(p, source_hash)[1],
                               'raw': json.loads(p.read_text())}
        expected = contracts[path]['by_page'].get(block['page'])
        if item != expected or block.get('chapter_id') != item['part_id']:
            raise ValueError('Document part block role/page/contract changed')
        source = block.get('sources') or [block]
        review = contracts[path]['raw']['parts']
        record = next(r for r in review if r['part_id'] == item['part_id'])
        hashes = {e['page']: e['evidence_sha256'] for e in record['review']['evidence_pages']}
        if any(s.get('page', block['page']) not in item['pages']
               or s.get('source_image_sha256') != hashes[s.get('page', block['page'])] for s in source):
            raise ValueError('Document part block source image/ownership differs')
        plan = plans.setdefault(item['part_id'], {**item, 'block_ids': [], 'title_block_id': None,
                                                  'source_pages': []})
        plan['block_ids'].append(block.get('block_id') or block['id'])
        plan['source_pages'].extend(s.get('page', block['page']) for s in source)
        if block.get('type') == 'heading' and part_title_matches(block['text'], item):
            if plan['title_block_id'] or block['page'] != item['pages'][0] or block.get('heading_depth') != 1:
                raise ValueError('Document part title is duplicated/out of position')
            plan['title_block_id'] = block.get('block_id') or block['id']
    for plan in plans.values():
        plan['source_pages'] = sorted(set(plan['source_pages']))
        if plan['source_pages'] != plan['pages'] or not plan['title_block_id']:
            raise ValueError('Document part lacks complete pages or reviewed title heading')
    return plans
