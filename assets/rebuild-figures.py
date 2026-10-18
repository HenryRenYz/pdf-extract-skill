#!/usr/bin/env python3
"""Rebuild packaged drawings; output is separate from accepted book images."""
import argparse
import importlib.util
import importlib.metadata
import json
import shutil
import subprocess
from pathlib import Path


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--figure', help='one figure ID; omitted means all redraws')
    parser.add_argument('--backend', choices=['all', 'matplotlib', 'recipe'], default='all')
    parser.add_argument('--output', required=True, type=Path, help='fresh directory outside the source package')
    parser.add_argument('--formats', nargs='+', choices=['pdf', 'png', 'svg'], default=['pdf'])
    parser.add_argument('--runtime', action='append', default=[], metavar='VERSION=PYTHON',
                        help='use a matching Python environment for a recorded Matplotlib version')
    parser.add_argument('--exact-runtime', action='store_true', help='require the recorded Matplotlib version')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = args.output.resolve()
    if output == root.parent or root.parent in output.parents or output in root.parent.parents:
        parser.error('Choose an output directory outside the accepted source package')
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    entries = manifest['figures']
    if args.figure:
        entries = [x for x in entries if x['figure_id'] == args.figure]
        if not entries:
            parser.error('Unknown figure ID')
        if not entries[0].get('script') and not entries[0].get('recipe'):
            parser.error('This figure retains its original image and has no redraw script')
    renderer = None
    runtimes = dict(item.split('=', 1) for item in args.runtime)
    current_version = None
    warned = set()
    for entry in entries:
        if args.backend == 'matplotlib' and entry.get('recipe') or args.backend == 'recipe' and not entry.get('recipe'):
            continue
        if entry.get('recipe'):
            if 'svg' in args.formats and entry['recipe']['backend'] != 'svg':
                parser.error('SVG output is supported only by SVG recipes; choose PDF/PNG for this backend')
            import sys
            sys.path.insert(0, str(root))
            from figure_recipe import rebuild_recipe
            pdf = rebuild_recipe(entry['recipe'], root / entry['recipe_folder'], output / entry['figure_id'])
            if 'svg' in args.formats:
                shutil.copyfile(root / entry['recipe_folder'] / entry['recipe']['source'], pdf.with_suffix('.svg'))
            if 'png' in args.formats:
                import fitz
                with fitz.open(pdf) as doc:
                    doc[0].get_pixmap(matrix=fitz.Matrix(400 / 72, 400 / 72)).save(pdf.with_suffix('.png'))
            print(entry['figure_id'])
            continue
        if not entry.get('script'):
            continue
        if renderer is None:
            renderer = load_module('packaged_figure_renderer', root / 'renderer.py')
            current_version = importlib.metadata.version('matplotlib')
        required_version = entry['matplotlib_version']
        if required_version != current_version:
            if required_version in runtimes:
                subprocess.run([runtimes[required_version], str(root / 'rebuild.py'), '--figure', entry['figure_id'],
                                '--output', str(output), '--exact-runtime', '--formats', *args.formats], check=True)
                continue
            if args.exact_runtime:
                parser.error('Use Matplotlib ' + required_version + ' or provide --runtime VERSION=PYTHON')
            if required_version not in warned:
                print('Note: recorded Matplotlib ' + required_version + '; current ' + current_version
                      + ' may differ in rendering. See matching requirements file.')
                warned.add(required_version)
        module = load_module('plot_' + entry['figure_id'].replace('-', '_'), root / entry['script'])
        renderer.render(module.draw, root / entry['spec'], root / entry['style'],
                        output / entry['figure_id'], root / entry['script'], tuple(args.formats))
        print(entry['figure_id'])


if __name__ == '__main__':
    main()
