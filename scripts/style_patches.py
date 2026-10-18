"""Apply explicit, text-bound typography patches; rule IDs never imply actions."""
from copy import deepcopy
from semantic_contract import text_hash, validate_style_spans


def attributes(kind):
    return {'normal': {'weight': 'normal', 'slant': 'upright'},
            'bold': {'weight': 'bold'}, 'italic': {'weight': 'normal', 'slant': 'italic'},
            'bold_italic': {'weight': 'bold', 'slant': 'italic'},
            'upright': {'slant': 'upright'}, 'bold_math': {'weight': 'bold'},
            'double_struck': {'family': 'double_struck'}, 'kai': {'family': 'kai'},
            'underline': {'decoration': 'underline'}}[kind]


def validate_rules(rules, source_hash):
    if (not isinstance(rules, dict) or rules.get('source_sha256') != source_hash
            or rules.get('review_status') != 'reviewed' or not rules.get('version')):
        raise ValueError('Typography rules require current source, reviewed status and version')
    entries = rules.get('rules')
    if not isinstance(entries, list) or any(not isinstance(r, dict) or not r.get('id') for r in entries):
        raise ValueError('Typography rules require rule objects with IDs')
    if len({r['id'] for r in entries}) != len(entries):
        raise ValueError('Duplicate typography rule IDs')


def apply_style_patches(data, documents, rules, source_hash):
    validate_rules(rules, source_hash)
    result, changes, seen = deepcopy(data), [], set()
    by_id = {b['id']: b for b in result['blocks']}
    by_rule = {r['id']: r for r in rules['rules']}
    for document in documents:
        if document.get('page') != data['page']:
            continue
        if document.get('source_sha256') != source_hash or document.get('rule_version') != rules['version']:
            raise ValueError('Style patch source/rule version differs')
        if not isinstance(document.get('patches'), list):
            raise ValueError('Style patch document requires a patches list')
        for patch in document['patches']:
            ident = patch.get('block_id')
            block = by_id.get(ident)
            if block is None or ident in seen or patch.get('base_text_sha256') != text_hash(block['text']):
                raise ValueError('Style patch target duplicated, missing or text changed')
            seen.add(ident)
            styles = deepcopy(patch.get('styles'))
            errors = validate_style_spans(styles, block['text'])
            if errors:
                raise ValueError('Invalid style patch: ' + '; '.join(errors))
            for style in styles:
                if style['evidence'] not in {'rule_inferred', 'uncertain'}:
                    raise ValueError('Inference patches cannot self-certify source_checked styles')
                if style['evidence'] == 'rule_inferred':
                    rule = by_rule.get(style.get('rule_id'))
                    if rule is None or style.get('rule_version') != rules['version']:
                        raise ValueError('Unknown/stale typography rule')
                    scope = rule.get('scope', {})
                    if not isinstance(scope, dict) or set(scope) - {'pages', 'types', 'chapter_ids'}:
                        raise ValueError('Executable rule scope requires pages/types/chapter_ids')
                    for key, value in [('pages', data['page']), ('types', block['type']),
                                       ('chapter_ids', block.get('chapter_id'))]:
                        if key in scope and (not isinstance(scope[key], list) or value not in scope[key]):
                            raise ValueError('Style patch is outside typography rule scope')
            confirmed = [s for s in block['styles'] if s.get('evidence') == 'source_checked']
            inferred = []
            for style in styles:
                overlaps = [s for s in confirmed if max(s['start'], style['start']) < min(s['end'], style['end'])]
                for prior in overlaps:
                    old, new = attributes(prior['kind']), attributes(style['kind'])
                    if any(old[key] != new[key] for key in old.keys() & new.keys()):
                        raise ValueError('Style inference conflicts with an already source-confirmed span')
                if not any(all(prior[k] == style[k] for k in ('start', 'end', 'kind')) for prior in overlaps):
                    inferred.append(style)
            before = block['styles']
            block['styles'] = sorted(confirmed + inferred, key=lambda s: (s['start'], s['end'], s['kind']))
            changes.append({'block_id': ident, 'before': before, 'after': deepcopy(block['styles']),
                            'kind': 'explicit_style_patch', 'rule_version': rules['version']})
            for item in patch.get('uncertainties', []):
                if not isinstance(item, dict) or not item.get('reason'):
                    raise ValueError('Style patch uncertainty requires a reason')
                result['uncertainties'].append({**item, 'block_id': ident, 'bbox': item.get('bbox', block['bbox'])})
    return result, changes
