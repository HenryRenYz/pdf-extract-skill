#!/usr/bin/env python3
"""Package accepted LaTeX images plus their selected, portable drawing sources."""
import argparse
import ast
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import tokenize
import zipfile
from pathlib import Path
from figure_recipe import bundle_recipe, rebuild_recipe
from source_identity import file_hash
from body_font_bundle import prepare_body_fonts
from latex_delivery import build_instructions

try:
    from figure_fallback import validate_fallback
except ModuleNotFoundError:
    from .figure_fallback import validate_fallback


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checked(path, expected=None):
    path = Path(path).resolve()
    if not path.is_file() or (expected and digest(path) != expected):
        raise ValueError('Missing or changed drawing dependency: ' + str(path))
    return path


def relocate_font_literals(text, stage):
    """Change only font-file string tokens, keeping drawing geometry untouched."""
    offsets, pos = [], 0
    for line in text.splitlines(keepends=True):
        offsets.append(pos)
        pos += len(line)
    edits, records = [], []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.STRING:
            continue
        try:
            value = ast.literal_eval(token.string)
        except (SyntaxError, ValueError):
            continue
        if not isinstance(value, str) or not Path(value).is_absolute() or Path(value).suffix.lower() not in {'.ttf', '.ttc', '.otf'}:
            continue
        source = checked(value)
        relative = 'shared/fonts/' + digest(source)[:12] + '-' + source.name
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
        expression = 'str(Path(__file__).resolve().parents[2] / ' + repr(relative) + ')'
        start = offsets[token.start[0] - 1] + token.start[1]
        end = offsets[token.end[0] - 1] + token.end[1]
        edits.append((start, end, expression))
        records.append({'original_font': source.name, 'package_path': relative, 'sha256': digest(source)})
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    ast.parse(text)
    return text, records


def write_source_archive(args, report):
    dist = args.dist.resolve()
    sidecar = args.export_manifest.resolve()
    source_files = [p for p in sorted(dist.rglob('*')) if p.is_file() and p.name != 'main.pdf']
    forbidden = [p for p in source_files if '__pycache__' in p.parts or p.suffix in {'.pyc', '.aux', '.toc', '.out', '.log'}]
    cache_dirs = {path.parent for path in forbidden if '__pycache__' in path.parts}
    for cache in cache_dirs:
        shutil.rmtree(cache)
    noncache = [path for path in forbidden if '__pycache__' not in path.parts]
    if noncache:
        raise ValueError('Unrelated work file in source package: ' + str(noncache[0]))
    source_files = [p for p in sorted(dist.rglob('*')) if p.is_file() and p.name != 'main.pdf']
    archive = args.zip.resolve()
    if archive == dist or dist in archive.parents:
        raise ValueError('ZIP must be outside the package directory')
    font_files, font_manifest = prepare_body_fonts(args)
    if font_files and any(p.relative_to(dist).parts[0] == 'body-fonts' for p in source_files):
        raise ValueError('Source already contains body-fonts; do not overwrite its resources')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in source_files:
            relative = path.relative_to(dist)
            if font_manifest and relative.as_posix() == 'BUILD.txt' and report.get('font_dependencies'):
                dependencies = {**report['font_dependencies'], 'fonts_bundled': True}
                z.writestr('BUILD.txt', build_instructions(dependencies))
            else:
                z.write(path, relative)
        for relative, value in font_files.items():
            if isinstance(value, Path):
                z.write(value, relative)
            else:
                z.writestr(relative, value)
    report['body_font_bundle'] = font_manifest
    report['source_archive'] = {'path': str(archive), 'sha256': digest(archive),
                                'files': len(source_files) + len(font_files),
                                'body_fonts_included': bool(font_manifest)}
    report['package_files'] = [{'path': str(p.relative_to(dist)), 'sha256': digest(p)}
                               for p in sorted(dist.rglob('*')) if p.is_file()]
    sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report['source_archive']


def package(args):
    dist = args.dist.resolve()
    sidecar = args.export_manifest.resolve()
    if args.zip.resolve().is_relative_to(dist) or sidecar.is_relative_to(dist):
        raise ValueError('ZIP and export manifest must be outside the accepted package')
    if getattr(args, 'verify_drawings', False) and (not args.verify_output or args.verify_output.resolve().is_relative_to(dist)
                                or dist.is_relative_to(args.verify_output.resolve())):
        raise ValueError('Drawing verification requires a separate output outside the accepted package')
    report = read_json(sidecar)
    declared = report.get('package_files')
    if not isinstance(declared, list) or not declared:
        raise ValueError('Export manifest must include the accepted package file inventory')
    for item in declared:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Export package inventory contains an unsafe path')
        checked(dist / relative, item['sha256'])
    if not report.get('figures'):
        return write_source_archive(args, report)
    registry_input = report.get('inputs', {}).get('figures', {}).get('path')
    registry_path = args.figures or (Path(registry_input) if registry_input else None)
    if registry_path:
        expected_registry = report.get('inputs', {}).get('figures', {}).get('sha256')
        checked(registry_path, expected_registry)
        registry_data = read_json(registry_path)
    else:
        raise ValueError('Selected images require an explicit figure registry')
    if registry_data.get('source_sha256') != report['source_sha256']:
        raise ValueError('Registry and LaTeX package have different source hashes')
    registry = {x.get('figure_id', x.get('id')): x for x in registry_data['figures']}
    recipes = {x['id']: x for x in read_json(args.drawing_inputs)} if args.drawing_inputs else {}
    assets = Path(__file__).resolve().parent.parent / 'assets'
    renderer = checked(args.renderer or Path(__file__).with_name('figure_render.py'))
    target = dist / 'figure-code'
    if target.exists() and not args.force:
        raise ValueError('figure-code exists; use --force only after review')
    stage = Path(tempfile.mkdtemp(prefix='drawing-bundle-', dir=dist.parent))
    try:
        shutil.copyfile(renderer, stage / 'renderer.py')
        shutil.copyfile(assets / 'rebuild-figures.py', stage / 'rebuild.py')
        manifest = {'schema_version': 1, 'source_sha256': report['source_sha256'],
                    'renderer_sha256': digest(renderer), 'figures': [],
                    'policy': 'Selected final images and drawing dependencies only; rebuild output is separate.'}
        verification = []
        recipe_verification = []
        matplotlib_versions = set()
        for ident, selected in report['figures'].items():
            if not isinstance(ident, str) or Path(ident).name != ident or ident in {'.', '..'}:
                raise ValueError('Figure ID must be a single safe path component')
            entry = registry[ident]
            checked(dist / selected['package_path'], selected['sha256'])
            item = {'figure_id': ident, 'image': '../' + selected['package_path'],
                    'image_sha256': selected['sha256'], 'status': selected['status']}
            if entry.get('build_recipe'):
                recipe = bundle_recipe(entry['build_recipe'], stage / 'figures' / ident, registry_path.parent)
                item.update(route='recipe', recipe=recipe, recipe_folder='figures/' + ident)
                if args.verify_drawings:
                    if not args.verify_output:
                        raise ValueError('Recipe verification requires --verify-output')
                    replay = rebuild_recipe(recipe, stage / item['recipe_folder'], args.verify_output / ident)
                    import fitz
                    accepted = checked(dist / selected['package_path'], selected['sha256'])
                    with fitz.open(accepted) as left, fitz.open(replay) as right:
                        exact = len(left) == len(right) and all(
                            a.rect == b.rect and a.get_pixmap(matrix=fitz.Matrix(2, 2)).samples == b.get_pixmap(matrix=fitz.Matrix(2, 2)).samples
                            for a, b in zip(left, right))
                    if not exact:
                        raise ValueError('Figure recipe replay differs from the accepted asset: ' + ident)
                    item['replay_verification'] = {'exact_accepted_render': True}
                recipe_verification.append(item)
                manifest['figures'].append(item)
                continue
            if not entry.get('script_path'):
                if selected['status'] not in {'retained_source', 'accepted_source'}:
                    raise ValueError('Redrawn figure lacks selected drawing code: ' + ident)
                assessment = validate_fallback(entry, dist / selected['package_path'])
                item['route'] = 'retained_original'
                # Source evidence stays outside the clean package; keep its
                # identity and decision so the reason is still inspectable.
                item['fallback_assessment'] = {k: v for k, v in assessment.items()
                                               if k not in {'evidence', 'attempts'}}
                manifest['figures'].append(item)
                continue
            recipe = recipes.get(ident, {
                'script': entry['script_path'], 'spec': entry.get('spec_path'),
                'style': entry.get('style_path'), 'metadata': entry.get('render_metadata_path')})
            if any(not recipe.get(key) for key in ('script', 'spec', 'style', 'metadata')):
                raise ValueError('Drawing needs explicit script/spec/style/metadata paths: ' + ident)
            meta = read_json(checked(recipe['metadata']))
            matplotlib_versions.add(meta['matplotlib_version'])
            if not any(x.get('sha256') == selected['sha256'] for x in meta['files'].values()):
                raise ValueError('Render metadata does not describe selected final image: ' + ident)
            if meta['exporter_sha256'] != digest(renderer):
                raise ValueError('Use the renderer version that produced the final figure: ' + ident)
            script = checked(recipe['script'])
            spec = checked(recipe['spec'], meta['spec_sha256'])
            style = checked(recipe['style'], meta['style_sha256'])
            if read_json(spec)['source']['pdf_sha256'] != report['source_sha256']:
                raise ValueError('Drawing spec refers to a different original PDF: ' + ident)
            original_script_matches = digest(script) == meta['script_sha256']
            if not original_script_matches and not args.verify_drawings:
                raise ValueError('Script changed after final render; verify its replay before packaging: ' + ident)
            folder = stage / 'figures' / ident
            folder.mkdir(parents=True)
            text, relocations = relocate_font_literals(script.read_text(encoding='utf-8'), stage)
            (folder / 'draw.py').write_text(text, encoding='utf-8')
            shutil.copyfile(spec, folder / 'spec.json')
            style_relative = 'shared/styles/' + digest(style)[:12] + '/style.json'
            style_target = stage / style_relative
            style_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(style, style_target)
            profile = read_json(style)
            for font in profile['font'].get('paths', []):
                font_path = Path(font)
                if font_path.is_absolute() or '..' in font_path.parts:
                    raise ValueError('Style font paths must be package-relative: ' + font)
                font_source = checked(style.parent / font)
                font_target = style_target.parent / font
                font_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(font_source, font_target)
            item.update(route='redraw', script=f'figures/{ident}/draw.py', spec=f'figures/{ident}/spec.json',
                        matplotlib_version=meta['matplotlib_version'],
                        style=style_relative, original_script_sha256=digest(script),
                        packaged_script_sha256=digest(folder / 'draw.py'),
                        recorded_render_script_sha256=meta['script_sha256'],
                        original_script_matches_record=original_script_matches,
                        font_path_relocations=relocations,
                        expected_original_png_sha256=meta['files']['png']['sha256'])
            for key in ('data_path', 'scene_path'):
                if entry.get(key):
                    source = checked(entry[key])
                    name = 'data.json' if key == 'data_path' else 'scene.json'
                    shutil.copyfile(source, folder / name)
                    item[name.split('.')[0]] = f'figures/{ident}/{name}'
            manifest['figures'].append(item)
            verification.append((item, checked(Path(recipe['metadata']).parent / 'figure.png', meta['files']['png']['sha256'])))
        license_dir = stage / 'shared' / 'licenses'
        license_dir.mkdir(parents=True, exist_ok=True)
        for path in args.font_license:
            source = checked(path)
            shutil.copyfile(source, license_dir / (source.parent.name + '-' + source.name))
        (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        for name in ('figure_recipe.py', 'asset_contract.py', 'source_identity.py'):
            shutil.copyfile(Path(__file__).with_name(name), stage / name)
        if not matplotlib_versions:
            requirements = ['PyMuPDF']
            if any(item.get('recipe', {}).get('backend') == 'svg' for item in manifest['figures']):
                requirements.append('CairoSVG')
            (stage / 'requirements.txt').write_text('\n'.join(requirements) + '\n')
            (stage / 'README.md').write_text(
                'Run python figure-code/rebuild.py --output /absolute/fresh/rebuilt from the source package root.\n'
                'Retained-original projects need no rebuild; recipes use sources and dependencies inside the package. SVG needs CairoSVG, TikZ needs XeLaTeX.\n'
                'Output must not overwrite accepted images in the package; after changes, view the actual render again.\n')
            if target.exists():
                shutil.rmtree(target)
            stage.rename(target)
            report['drawing_bundle'] = {'path': 'figure-code', 'figures': len(manifest['figures']),
                                       'redraw_scripts': 0, 'build_recipes': len(recipe_verification),
                                       'all_replayed_exactly': bool(args.verify_drawings),
                                       'manifest_sha256': digest(target / 'manifest.json')}
            return write_source_archive(args, report)
        dependency_names = ['matplotlib', 'numpy', 'scipy', 'Pillow', 'fonttools']
        versions = json.loads(subprocess.check_output([
            args.python, '-c', 'import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in '
            + repr(dependency_names) + '}))'], text=True))
        runtime_paths = dict(item.split('=', 1) for item in args.runtime)
        runtime_paths.setdefault(versions['matplotlib'], args.python)
        runtime_versions = {versions['matplotlib']: versions}
        for version in matplotlib_versions - {versions['matplotlib']}:
            if version not in runtime_paths:
                if args.verify_drawings:
                    raise ValueError('Replay verification needs --runtime ' + version + '=PYTHON')
                continue
            deps = json.loads(subprocess.check_output([
                runtime_paths[version], '-c', 'import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in '
                + repr(dependency_names) + '}))'], text=True))
            if deps['matplotlib'] != version:
                raise ValueError('Runtime mapping version does not match actual Matplotlib')
            runtime_versions[version] = deps
        if matplotlib_versions and not matplotlib_versions <= set(runtime_paths) and not args.verify_drawings:
            raise ValueError('Choose the drawing environment matching the recorded Matplotlib version')
        manifest['recorded_matplotlib_versions'] = sorted(matplotlib_versions)
        manifest['verified_runtime_versions'] = runtime_versions if args.verify_drawings else None
        (stage / 'requirements.txt').write_text(''.join(n + '==' + versions[n] + '\n' for n in dependency_names))
        for version, deps in runtime_versions.items():
            (stage / ('requirements-mpl-' + version + '.txt')).write_text(''.join(n + '==' + deps[n] + '\n' for n in dependency_names))
        (stage / 'README.md').write_text(
            '# Drawing code\n\nEach final image is in `../images/`; its script, spec, and data are in `figures/<figure_id>/`.'
            'Retained-original projects have no fabricated redraw script. manifest.json maps figure numbers, image files, and code one-to-one.\n\n'
            'Use a Python 3.11 environment; install requirements.txt, then run:\n\n'
            '```bash\npython figure-code/rebuild.py --output /absolute/fresh/rebuilt --formats pdf\n'
            'python figure-code/rebuild.py --figure FIGURE_ID --output /absolute/fresh/one --formats png\n```\n\n'
            'Some figures come from older Matplotlib versions. manifest records the original version of each figure, and requirements-mpl-VERSION.txt provides the matching dependencies.'
            'To match the original rendering, create the matching environment and append `--exact-runtime --runtime VERSION=/path/to/python`;'
            'the unified entry point dispatches to that environment. By default you can also redraw with the current environment, but different software versions may cause minor differences.\n\n'
            'Invoke from the source package root; the output directory must be outside the source package. By default it does not overwrite accepted images.'
            'PNG output uses 400dpi throughout. After redrawing, inspect first, then replace the corresponding files in images.\n\n'
            'The shared renderer, frozen styles, and required fonts are included. Scripts only relocate font files; they do not change geometry or data;'
            'see manifest for the hashes and migration records of the original and packaged scripts. Original project paths inside spec/scene are provenance records only;'
            'the original working directory need not exist, and the PDF need not be identified again.\n')
        if args.verify_drawings:
            if not args.verify_output:
                raise ValueError('--verify-drawings requires a fresh --verify-output outside the package')
            command = [args.python, str(stage / 'rebuild.py'), '--output', str(args.verify_output.resolve()), '--exact-runtime']
            for version, executable in runtime_paths.items():
                command.extend(['--runtime', version + '=' + executable])
            command.extend(['--formats', 'png'])
            if recipe_verification:
                command.extend(['--backend', 'matplotlib'])
            if not args.reuse_replays:
                subprocess.run(command, check=True)
            from PIL import Image
            for item, original in verification:
                replay = args.verify_output / item['figure_id'] / 'figure.png'
                replay_meta = read_json(replay.parent / 'render-metadata.json')
                expected_inputs = {
                    'script_sha256': digest(stage / item['script']),
                    'spec_sha256': digest(stage / item['spec']),
                    'style_sha256': digest(stage / item['style']),
                    'exporter_sha256': digest(stage / 'renderer.py'),
                    'matplotlib_version': item['matplotlib_version']}
                if any(replay_meta.get(k) != v for k, v in expected_inputs.items()):
                    raise ValueError('Replay dependency hashes changed; run a fresh replay: ' + item['figure_id'])
                checked(replay, replay_meta['files']['png']['sha256'])
                with Image.open(original) as left, Image.open(replay) as right:
                    exact = left.size == right.size and left.convert('RGBA').tobytes() == right.convert('RGBA').tobytes()
                if not exact:
                    raise ValueError('Packaged code does not reproduce accepted pixels: ' + item['figure_id'])
                item['replay_verification'] = {'exact_original_pixels': True,
                                               'replayed_png_sha256': digest(replay)}
            (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        if target.exists():
            shutil.rmtree(target)
        stage.rename(target)
        report['drawing_bundle'] = {'path': 'figure-code', 'figures': len(manifest['figures']),
                                    'redraw_scripts': len(verification),
                                    'all_replayed_exactly': bool(args.verify_drawings),
                                    'manifest_sha256': digest(target / 'manifest.json')}
        return write_source_archive(args, report)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--export-manifest', type=Path, required=True)
    parser.add_argument('--zip', type=Path, required=True)
    parser.add_argument('--figures', type=Path)
    parser.add_argument('--drawing-inputs', type=Path, help='explicit per-ID script/spec/style/metadata mapping for legacy registries')
    parser.add_argument('--renderer', type=Path)
    parser.add_argument('--font-license', type=Path, action='append', default=[])
    parser.add_argument('--include-body-fonts', action='store_true',
                        help='explicit opt-in; add selected body fonts and licenses to the ZIP only')
    parser.add_argument('--body-font', type=Path, action='append', default=[])
    parser.add_argument('--body-font-license', type=Path, action='append', default=[])
    parser.add_argument('--verify-drawings', action='store_true')
    parser.add_argument('--verify-output', type=Path)
    parser.add_argument('--reuse-replays', action='store_true', help='reuse actual replays only after checking all dependency/output hashes')
    parser.add_argument('--python', default='python')
    parser.add_argument('--runtime', action='append', default=[], metavar='VERSION=PYTHON')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    print(json.dumps(package(args), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
