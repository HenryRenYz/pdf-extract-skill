"""Apply a reviewed book rule to structural labels without editing source text."""
from copy import deepcopy
from hashlib import sha256
import re
import math


def valid_gap(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 6

PATTERN = re.compile(r"^(?P<open>\*\*\*|\*\*|)(?P<label>(?:例|定理|定义|引理|命题|推论)\s*\d+(?:[.．]\d+)*|证明|证|解|定义)(?P=open)")

def annotate_inline_labels(blocks, rule):
    if (rule.get('review', {}).get('source_checked') is not True
            or not rule.get('id') or not rule.get('version') or not valid_gap(rule.get('gap_ccwd'))):
        raise ValueError('Inline label rule requires source review, identity and a finite 0..6 character gap')
    result, decisions = deepcopy(blocks), []
    for block in result:
        if block.get('source_sha256') != rule.get('source_sha256'):
            raise ValueError('Inline label rule belongs to a different source edition')
        if block['type'] not in {'example', 'theorem', 'proof', 'paragraph', 'exercise'}:
            continue
        text = block['text']
        match = PATTERN.match(text)
        if not match:
            continue
        if match['label'] == '定义' and '定义' not in rule.get('unnumbered_labels', []):
            # A prose occurrence is not automatically a structural label.
            continue
        if match['label'] == '证' and block['type'] != 'proof':
            # The short printed proof label is structural only in a proof;
            # ordinary bold words such as “证据” must not gain a label gap.
            continue
        if match['label'] not in {'解', '证明', '证', '定义'} and block['type'] not in {'example', 'theorem'}:
            # Emphasized prose references still are not example/theorem headings.
            continue
        a, b = match.span('label')
        bold = bool(match['open']) or any(s.get('kind') in {'bold', 'bold_italic'}
                   and s['start'] <= a and s['end'] >= b
                   and not text[b:s['end']].strip() for s in block.get('styles', []))
        if not bold:
            continue
        end = match.end()
        separator = re.match(r'[ \t\u3000\u00a0]*', text[end:]).group()
        stop = end + len(separator)
        if stop == len(text):
            continue
        binding = {'start': 0, 'end': end, 'separator_end': stop,
                   'label': match['label'], 'gap_ccwd': rule['gap_ccwd'],
                   'text_sha256': sha256(text.encode()).hexdigest(),
                   'evidence': 'rule_inferred', 'rule_id': rule['id'],
                   'rule_version': rule['version'], 'source_sha256': rule['source_sha256']}
        if block.get('inline_label') not in (None, binding):
            raise ValueError('Conflicting inline label binding: ' + block.get('block_id', block['id']))
        block['inline_label'] = binding
        decisions.append({'block_id': block.get('block_id', block['id']), **binding})
    return result, decisions

def validate_inline_label(block):
    binding = block.get('inline_label')
    if binding is None:
        return None
    text = block['text']
    a, b, stop = (binding.get(k) for k in ('start', 'end', 'separator_end'))
    if (any(type(v) is not int for v in (a, b, stop)) or a != 0
            or not 0 < b <= stop < len(text) or not valid_gap(binding.get('gap_ccwd'))
            or binding.get('text_sha256') != sha256(text.encode()).hexdigest()
            or binding.get('source_sha256') != block.get('source_sha256')
            or binding.get('evidence') not in ('source_checked', 'rule_inferred')
            or not binding.get('rule_id') or not binding.get('rule_version')
            or text[b:stop].strip()):
        raise ValueError('Invalid/stale inline label binding')
    match = PATTERN.fullmatch(text[a:b])
    if not match or match['label'] != binding.get('label'):
        raise ValueError('Inline label text/range mismatch')
    return binding
