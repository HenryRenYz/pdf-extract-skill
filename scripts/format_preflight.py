"""Hash-bound template admission; artifacts prove identity, reviews prove intent.

The gate verifies real files, a readable compiled PDF and explicit source-review
records. It cannot independently establish the truth of a reviewer's visual QA.
Paths in a profile/ready record are relative to that JSON file unless absolute.
"""
import hashlib
import ast
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
try:
    from latex_template import load_template, template_hashes
except ModuleNotFoundError:
    from .latex_template import load_template, template_hashes

SCHEMA_VERSION = 1
PREFLIGHT_MAX_PAGES = 8
TEMPLATE_FILES = ('book-template.tex', 'book-style.sty')
def local_tool_dependencies():
    """Include the actual transitive local imports, including conditional ones."""
    root = Path(__file__).resolve().parent
    pending = ['export_latex.py', 'assemble_book.py', 'validate_outputs.py', 'format_preflight.py']
    found = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        for node in ast.walk(ast.parse((root / name).read_text())):
            modules = [node.module] if isinstance(node, ast.ImportFrom) else ([v.name for v in node.names] if isinstance(node, ast.Import) else [])
            for module in modules:
                candidate = (module or '').split('.')[0] + '.py'
                if (root / candidate).is_file() and candidate not in found:
                    pending.append(candidate)
    return tuple(sorted(found))


DEFAULT_TOOLS = local_tool_dependencies()
RENDER_DEFAULTS = {'nominal_page_mm': 170, 'figure_scale': 1.4, 'text_width_mm': 164,
                   'paper_width_mm': 210, 'paper_height_mm': 297,
                   'cjk_font': 'Noto Serif CJK SC', 'latin_font': 'Noto Serif',
                   'cjk_emphasis_font': 'Noto Sans CJK SC', 'latin_sans_font': 'Noto Sans',
                   'cjk_kai_font': 'KaiTi', 'engine': 'xelatex', 'language':'zh'}


def render_configuration(profile):
    configured = profile.get('render_configuration', {})
    if not isinstance(configured, dict) or set(configured) - set(RENDER_DEFAULTS):
        raise ValueError('Unknown render_configuration fields')
    result = {**RENDER_DEFAULTS, **configured}
    if result['language'] not in ('zh','en'):
        raise ValueError('Supported render language values: zh, en')
    if profile.get('template_dir'):
        directory = Path(profile['template_dir']).expanduser()
        if not directory.is_absolute() and profile.get('profile_path'):
            directory = Path(profile['profile_path']).parent / directory
        if (directory / 'template.json').is_file():
            project = load_template(directory)
            if 'engine' in configured and configured['engine'] != project['engine']:
                raise ValueError('Render engine differs from template.json')
            result['engine'] = project['engine']
    for key, value in result.items():
        if key.endswith('_mm') or key == 'figure_scale':
            if type(value) not in (int, float) or not 0 < value < 10000:
                raise ValueError('Invalid render_configuration.' + key)
        elif not isinstance(value, str) or not value.strip() or '\n' in value:
            raise ValueError('Invalid render_configuration.' + key)
    return result


def render_environment(profile):
    result = {'python': sys.version.split()[0], 'libraries': {}, 'fonts': {}}
    for package in ('PyMuPDF', 'Pillow'):
        try:
            result['libraries'][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result['libraries'][package] = None
    config = render_configuration(profile)
    engine = shutil.which(config['engine'])
    result['engine'] = {'path': engine, 'sha256': _sha(engine) if engine else None}
    if engine:
        probe = subprocess.run([engine, '--version'], capture_output=True, text=True, timeout=15)
        result['engine']['version'] = probe.stdout.splitlines()[0] if probe.returncode == 0 and probe.stdout else None
    if shutil.which('fc-match'):
        for key, value in config.items():
            if not key.endswith('_font'):
                continue
            probe = subprocess.run(['fc-match', '-f', '%{file}', value], capture_output=True, text=True, timeout=10)
            path = Path(probe.stdout.strip())
            result['fonts'][key] = {'requested': value, 'file_sha256': _sha(path) if path.is_file() else None}
    return result
# These keys have native template hooks. Other profile fields remain descriptive.
SUPPORTED_TEMPLATE_KEYS = {'code': ('font_family', 'font_size_pt', 'line_spacing_pt', 'outer_indent_ccwd')}


def _sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError('Artifact missing/unreadable: ' + str(path)) from exc


def _hash(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError(name + ' must be a lowercase SHA256')
    return value


def _read(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Cannot read preflight JSON: ' + str(path)) from exc
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('Preflight JSON schema_version must be 1')
    return data


def _path(value, base, name, directory=False):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ' requires a real path')
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else base / path).resolve()
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(name + ' missing: ' + str(path))
    return path


def _record_file(record, base, name):
    if not isinstance(record, dict):
        raise ValueError(name + ' requires path and sha256')
    path = _path(record.get('path'), base, name)
    if _sha(path) != _hash(record.get('sha256'), name + '.sha256'):
        raise ValueError(name + ' file hash changed: ' + str(path))
    return path


def load_format_profile(path, source_sha256):
    """Validate a source-bound format profile and expose resolved template hooks."""
    path = Path(path).expanduser().resolve()
    data = _read(path)
    if data.get('source_sha256') != _hash(source_sha256, 'source_sha256'):
        raise ValueError('Format profile source hash differs from current PDF')
    if not isinstance(data.get('status'), str) or data['status'] not in {'draft', 'frozen'}:
        raise ValueError('Format profile status must be draft or frozen')
    if not isinstance(data.get('version'), str) or not data['version'].strip():
        raise ValueError('Format profile requires version')
    pages = data.get('representative_pages')
    if not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages) or len(pages) != len(set(pages)):
        raise ValueError('representative_pages requires unique positive physical pages')
    families = data.get('tested_families')
    if not isinstance(families, list) or not families or any(not isinstance(f, str) or not f.strip() for f in families) or len(families) != len(set(families)):
        raise ValueError('tested_families requires unique nonempty family names')
    template = _path(data.get('template_dir'), path.parent, 'template_dir', directory=True)
    project = load_template(template)
    render_configuration({**data, 'template_dir':str(template)})
    rules = data.get('template_rules', {})
    if not isinstance(rules, dict):
        raise ValueError('template_rules must be an object')
    code = rules.get('code', {})
    if not isinstance(code, dict):
        raise ValueError('template_rules.code must be an object')
    if 'font_family' in code and (not isinstance(code['font_family'], str) or not re.fullmatch(r'[\w .,+()-]{1,120}', code['font_family'])):
        raise ValueError('code font_family must be a plain font name')
    for key, low, high in [('font_size_pt', 4, 30), ('line_spacing_pt', 4, 60), ('outer_indent_ccwd', 0, 6)]:
        value = code.get(key)
        if key in code and (type(value) not in (int, float) or not low <= value <= high):
            raise ValueError('Unsupported code template value: ' + key)
    return {**data, 'profile_path': str(path), 'profile_sha256': _sha(path),
            'template_dir': str(template), 'template_project':project, 'supported_template_keys': SUPPORTED_TEMPLATE_KEYS,
            'supported_template_rules': {'code': {k: code[k] for k in SUPPORTED_TEMPLATE_KEYS['code'] if k in code}}}


def check_template_ready(profile_path, ready_path, source_sha256, tool_paths=None):
    """Return admission metadata or ValueError; never accept missing artifacts.

    tool_paths optionally maps tool basenames to actual paths. Mandatory current
    exporter/semantic/special/gate modules are always checked, including when an
    override mapping only contains a subset of tools.
    """
    profile = load_format_profile(profile_path, source_sha256)
    if profile['status'] != 'frozen':
        raise ValueError('Production requires a frozen format profile')
    path = Path(ready_path).expanduser().resolve()
    data = _read(path)
    if data.get('status') != 'accepted':
        raise ValueError('Template ready status must be accepted')
    if data.get('source_sha256') != source_sha256 or data.get('profile_sha256') != profile['profile_sha256']:
        raise ValueError('Template ready source/profile hash changed')
    if data.get('render_configuration') != render_configuration(profile):
        raise ValueError('Template ready must pin the actual render_configuration; regenerate legacy admission')
    if data.get('render_environment') != render_environment(profile):
        raise ValueError('Template ready render environment missing or changed; recompile and review')
    template = _path(data.get('template_dir'), path.parent, 'ready template_dir', directory=True)
    if str(template) != profile['template_dir']:
        raise ValueError('Template ready/profile template directory differs')
    hashes = data.get('template_files')
    expected_hashes = template_hashes(profile['template_project'])
    if not isinstance(hashes, dict) or set(hashes) != set(expected_hashes):
        raise ValueError('template_files must pin the complete current template resource inventory')
    for name in expected_hashes:
        if _sha(template / name) != _hash(hashes[name], 'template_files.' + name):
            raise ValueError('Template file hash changed: ' + name)
    tools = {name: Path(__file__).resolve().parent / name for name in DEFAULT_TOOLS}
    if tool_paths is not None:
        if not isinstance(tool_paths, dict):
            raise ValueError('tool_paths must map tool names to paths')
        try:
            if any(not isinstance(name, str) or not name for name in tool_paths):
                raise ValueError('tool_paths keys must be nonempty strings')
            tools.update({name: Path(value).expanduser().resolve() for name, value in tool_paths.items()})
        except TypeError as exc:
            raise ValueError('tool_paths values must be actual paths') from exc
    pinned = data.get('tool_sha256')
    if not isinstance(pinned, dict):
        raise ValueError('tool_sha256 requires actual exporter and semantic module hashes')
    # Unknown hashes must have a verifiable actual tool path, not self-reported identities.
    if set(pinned) != set(tools):
        raise ValueError('tool_sha256 does not match current verified tool set')
    for name, tool in tools.items():
        if not tool.is_file() or _sha(tool) != _hash(pinned.get(name), 'tool_sha256.' + name):
            raise ValueError('Actual tool hash changed/missing: ' + name)
    prompt_hashes = data.get('prompt_sha256')
    if prompt_hashes is not None:
        if not isinstance(prompt_hashes, dict) or set(prompt_hashes) != {'page-prompt.txt', 'style-prompt.txt'}:
            raise ValueError('prompt_sha256 must pin page-prompt.txt and style-prompt.txt')
        for name, checksum in prompt_hashes.items():
            if _sha(Path(__file__).resolve().parent.parent / 'assets' / name) != _hash(checksum, name):
                raise ValueError('Frozen prompt hash changed: ' + name)
    pdf = _record_file(data.get('compiled_pdf'), path.parent, 'compiled_pdf')
    if not profile['template_project']['legacy']:
        dependencies = data.get('template_dependencies')
        if not isinstance(dependencies, list) or not dependencies:
            raise ValueError('Template project admission requires actual recorder template_dependencies')
        seen_dependencies = set()
        for dependency in dependencies:
            if not isinstance(dependency, dict) or dependency.get('scope') not in ('package','environment'):
                raise ValueError('Invalid template dependency scope')
            dependency_name = dependency.get('path')
            if not isinstance(dependency_name, str) or dependency_name in seen_dependencies:
                raise ValueError('Template dependencies require unique paths')
            seen_dependencies.add(dependency_name)
            base = pdf.parent if dependency['scope'] == 'package' else path.parent
            _record_file(dependency, base, 'template dependency')
    if not pdf.read_bytes().startswith(b'%PDF-'):
        raise ValueError('compiled_pdf is not a real PDF')
    try:
        import fitz
        with fitz.open(pdf) as document:
            if document.page_count < 1 or document.needs_pass:
                raise ValueError('compiled_pdf must be readable and nonempty')
            output_pages = document.page_count
    except Exception as exc:
        raise ValueError('compiled_pdf cannot be opened as a readable PDF') from exc
    checks = data.get('checks')
    if not isinstance(checks, list) or not checks or any(not isinstance(c, dict) or not isinstance(c.get('name'), str) or c.get('status') != 'pass' for c in checks):
        raise ValueError('Every template check must have a name and status=pass')
    by_name = {c['name']: c for c in checks}
    if len(by_name) != len(checks) or not {'compile', 'source_fidelity', 'family_coverage'} <= set(by_name):
        raise ValueError('Template checks require unique compile/source_fidelity/family_coverage')
    covered = by_name['family_coverage'].get('families')
    if not isinstance(covered, list) or any(not isinstance(f, str) for f in covered) or not set(profile['tested_families']) <= set(covered):
        raise ValueError('Family checks do not cover all profile tested_families')
    reviews = data.get('source_checks')
    if not isinstance(reviews, list) or not reviews:
        raise ValueError('source_checks requires actual source evidence reviews')
    reviewed = set()
    for review in reviews:
        if not isinstance(review, dict) or type(review.get('page')) is not int or review['page'] < 1 or review.get('status') != 'pass':
            raise ValueError('Source check requires physical page and status=pass')
        if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
            raise ValueError('Source check requires independent reviewer identity')
        if type(review.get('output_page')) is not int or not 1 <= review['output_page'] <= output_pages:
            raise ValueError('Source check output_page is outside actual compiled PDF')
        evidence = _record_file(review.get('evidence'), path.parent, 'source_checks evidence')
        try:
            if evidence.read_bytes().startswith(b'%PDF-'):
                with fitz.open(evidence) as source_document:
                    if source_document.page_count < 1 or source_document.needs_pass:
                        raise ValueError('empty/encrypted source evidence')
            else:
                from PIL import Image
                with Image.open(evidence) as source_image:
                    source_image.verify()
        except Exception as exc:
            raise ValueError('source_checks evidence must be an actual readable source image/PDF') from exc
        reviewed.add(review['page'])
    if not set(profile['representative_pages']) <= reviewed:
        raise ValueError('Source checks omit representative source pages')
    if not isinstance(data.get('unresolved_issues', []), list) or data.get('unresolved_issues', []):
        raise ValueError('Template has unresolved issues')
    return {'status': 'accepted', 'source_sha256': source_sha256,
            'profile_path': profile['profile_path'], 'profile_sha256': profile['profile_sha256'],
            'ready_path': str(path), 'ready_sha256': _sha(path), 'template_dir': str(template),
            'template_files': hashes, 'tool_sha256': pinned, 'compiled_pdf': {'path': str(pdf), 'sha256': _sha(pdf)},
            'representative_pages': profile['representative_pages'], 'tested_families': profile['tested_families']}


def validate_preflight_scope(pages):
    if not pages or len(set(pages)) > PREFLIGHT_MAX_PAGES:
        raise ValueError(f'--preflight-mode allows at most {PREFLIGHT_MAX_PAGES} actual source pages; use a small sample')
