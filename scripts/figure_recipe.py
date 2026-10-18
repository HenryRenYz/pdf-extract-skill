"""Portable build recipes for SVG, standalone TikZ and recovered PDF artwork."""
import json
import shutil
import subprocess
from pathlib import Path
from asset_contract import resolve_asset
from source_identity import file_hash

BACKENDS = {'svg', 'tikz', 'native_pdf'}


def bundle_recipe(recipe, folder, base):
    backend = recipe.get('backend')
    if backend not in BACKENDS:
        raise ValueError('Unsupported figure build backend: ' + str(backend))
    source = resolve_asset(recipe.get('source'), base)
    expected_suffix = {'svg': '.svg', 'tikz': '.tex', 'native_pdf': '.pdf'}[backend]
    if source.suffix.lower() != expected_suffix:
        raise ValueError('Figure recipe source extension differs from backend')
    folder.mkdir(parents=True, exist_ok=True)
    name = 'source' + expected_suffix
    shutil.copyfile(source, folder / name)
    files = {name: file_hash(source)}
    dependencies = recipe.get('dependencies', {})
    if not isinstance(dependencies, dict):
        raise ValueError('Recipe dependencies must map package-relative names to asset references')
    for relative, reference in dependencies.items():
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or path.as_posix() == name:
            raise ValueError('Recipe dependency escapes or replaces source')
        dep = resolve_asset(reference, base)
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dep, target)
        files[path.as_posix()] = file_hash(dep)
    return {'backend': backend, 'source': name, 'files': files}


def rebuild_recipe(recipe, folder, output):
    folder, output = Path(folder), Path(output)
    if output.exists():
        raise ValueError('Recipe output must be a fresh directory')
    for relative, checksum in recipe['files'].items():
        candidate = (folder / relative).resolve()
        if not candidate.is_relative_to(folder.resolve()) or file_hash(candidate) != checksum:
            raise ValueError('Packaged figure recipe dependency changed')
    output.mkdir(parents=True)
    backend = recipe['backend']
    source = folder / recipe['source']
    if backend == 'native_pdf':
        shutil.copyfile(source, output / 'figure.pdf')
    elif backend == 'svg':
        import cairosvg
        # SVG units belong to the source. No universal 72/96dpi override.
        cairosvg.svg2pdf(url=str(source), write_to=str(output / 'figure.pdf'))
    elif backend == 'tikz':
        executable = shutil.which('xelatex')
        if not executable:
            raise ValueError('TikZ rebuild requires XeLaTeX')
        build = output / 'build'
        build.mkdir()
        command = [executable, '-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error',
                   '-output-directory=' + str(build.resolve()), source.name]
        completed = subprocess.run(command, cwd=folder, capture_output=True, text=True, timeout=180)
        (output / 'compile.log').write_text(completed.stdout)
        if completed.returncode:
            raise ValueError('TikZ recipe compilation failed; inspect retained compile.log')
        shutil.copyfile(build / (source.stem + '.pdf'), output / 'figure.pdf')
    else:
        raise ValueError('Unsupported packaged figure recipe')
    return output / 'figure.pdf'
