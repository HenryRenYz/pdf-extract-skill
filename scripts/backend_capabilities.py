"""Declarative, provider-neutral capabilities; a declaration is not a vision test."""
import json
from pathlib import Path
from PIL import Image
from source_identity import file_hash


def load_capabilities(path=None, *, backend=None, model=None, required=False):
    if path is None:
        if required:
            raise ValueError('Model execution requires --capabilities declaring image input, text output and tools')
        return None
    path = Path(path).expanduser().resolve(strict=True)
    data = json.loads(path.read_text())
    allowed = {'schema_version', 'backend', 'model', 'input_modalities', 'output_modalities',
               'tools', 'images', 'usage_semantics', 'evidence'}
    if not isinstance(data, dict) or set(data)-allowed or data.get('schema_version') != 1:
        raise ValueError('Unsupported capabilities schema/fields')
    if data.get('backend') != backend:
        raise ValueError('Capabilities backend differs from selected backend')
    if data.get('model') is not None and data['model'] != model:
        raise ValueError('Model-bound capabilities require the same explicit model')
    for field in ('input_modalities', 'output_modalities'):
        if not isinstance(data.get(field), list) or any(not isinstance(x, str) for x in data[field]):
            raise ValueError('Capability modalities must be string arrays')
    if 'image' not in data['input_modalities'] or 'text' not in data['output_modalities'] or data.get('tools') is not True:
        raise ValueError('Page/figure workers require image input, text output and tools')
    limits = data.get('images', {})
    if not isinstance(limits, dict) or set(limits)-{'max_attachments', 'max_edge', 'max_pixels', 'max_total_pixels'}:
        raise ValueError('Unsupported image capability limits')
    if any(type(x) is not int or x < 1 for x in limits.values()):
        raise ValueError('Image capability limits must be positive integers')
    semantics = data.get('usage_semantics', {})
    if (not isinstance(semantics, dict) or set(semantics)-{'input', 'output'}
            or semantics.get('input', 'unknown') not in {'uncached', 'includes_cache', 'unknown'}
            or semantics.get('output', 'unknown') not in {'includes_reasoning', 'separate_reasoning', 'unknown'}):
        raise ValueError('Unsupported token usage semantics')
    return {'sha256': file_hash(path), 'declaration': data,
            'verification': 'declared only; requires a real image/tool pilot for the actual runtime model'}


def check_images(capabilities, attachments, readable=()):
    """Count initial attachments; check per-image limits also on readable images.

    max_total_pixels covers initial attachments only. On-demand tool reads and
    provider resizing remain observable only through a separate runtime audit.
    """
    limits = capabilities['declaration'].get('images', {}) if capabilities else {}
    dimensions = []
    for path in attachments:
        with Image.open(path) as image:
            dimensions.append(image.size)
    total = sum(w*h for w, h in dimensions)
    if len(attachments) > limits.get('max_attachments', float('inf')) or total > limits.get('max_total_pixels', float('inf')):
        raise ValueError('Initial image attachments exceed declared capability budget')
    for path in {*map(Path, attachments), *map(Path, readable)}:
        with Image.open(path) as image:
            w, h = image.size
        if max(w, h) > limits.get('max_edge', float('inf')) or w*h > limits.get('max_pixels', float('inf')):
            raise ValueError('An attached/readable image exceeds declared per-image capability budget')
    return {'initial_attachments': len(attachments), 'initial_pixels': total,
            'limits': limits, 'status': 'within_declared_limits' if capabilities else 'undeclared',
            'scope': 'initial aggregate and attached/readable per-image sizes; not server-side delivery verification'}
