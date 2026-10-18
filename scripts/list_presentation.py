"""Source-reviewed paragraph indentation for printed algorithm/list steps."""
from hashlib import sha256
from pathlib import Path
import math


def validate_list_presentation(block):
    decision = block.get('list_presentation')
    if decision is None:
        return None
    if (not isinstance(decision, dict) or decision.get('schema_version') != 1
            or block.get('type') not in ('list', 'paragraph')
            or block.get('items') or block.get('footnotes')):
        raise ValueError('List indentation requires a reviewed flat text/list block')
    for key in ('source_sha256', 'source_image_sha256'):
        if decision.get(key) != block.get(key):
            raise ValueError('List indentation source/image identity changed')
    if decision.get('text_sha256') != sha256(block['text'].encode()).hexdigest():
        raise ValueError('List indentation text changed')
    review = decision.get('review', {})
    evidence = Path(review.get('evidence_path', ''))
    if (review.get('source_checked') is not True or not review.get('reviewer')
            or not evidence.is_file()
            or sha256(evidence.read_bytes()).hexdigest() != block.get('source_image_sha256')):
        raise ValueError('List indentation requires verified native source evidence')
    indent, hanging = decision.get('indent_ccwd'), decision.get('hanging_ccwd', 0)
    for value in (indent, hanging):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 6:
            raise ValueError('List indentation must be finite within six CJK character widths')
    if indent + hanging > 8:
        raise ValueError('List indentation leaves too little readable paragraph width')
    if decision.get('source_block_ids') != (block.get('source_block_ids') or [block.get('block_id', block.get('id'))]):
        raise ValueError('List indentation source ownership changed')
    return decision


def render_list_presentation(block, content):
    decision = validate_list_presentation(block)
    if decision is None:
        return content
    # TeX resets paragraph shape at the natural paragraph end. Do not force
    # \par before a following display: AMS would add a spurious empty line.
    first = decision['indent_ccwd']
    following = first + decision.get('hanging_ccwd', 0)
    return (r'\parshape=2 ' + f'{first:g}' + r'\ccwd '
            + r'\dimexpr\linewidth-' + f'{first:g}' + r'\ccwd\relax '
            + f'{following:g}' + r'\ccwd '
            + r'\dimexpr\linewidth-' + f'{following:g}' + r'\ccwd\relax'
            + '\n' + r'\noindent ' + content)
