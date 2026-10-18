#!/usr/bin/env python3
"""Read-only dependency/capability check for the selected PDF workflow."""
import argparse
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from backend_capabilities import load_capabilities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['extract', 'typeset', 'figures', 'ocr'], default='extract')
    parser.add_argument('--backend', choices=['none', 'opencode', 'codex'], default='none')
    parser.add_argument('--capabilities', type=Path)
    parser.add_argument('--model')
    args = parser.parse_args()
    try:
        capabilities = load_capabilities(args.capabilities, backend=args.backend, model=args.model, required=args.backend != 'none')
    except ValueError as error:
        parser.error(str(error))
    required = ['PIL', 'pymupdf']
    required += {'extract': [], 'typeset': [], 'figures': ['numpy', 'matplotlib', 'fontTools'],
                 'ocr': ['torch', 'transformers', 'mineru_vl_utils']}[args.mode]
    modules = {name: importlib.util.find_spec(name) is not None for name in required}
    programs = ['xelatex', 'pdftoppm'] if args.mode == 'typeset' else []
    if args.backend != 'none':
        programs.append(args.backend)
    commands = {}
    for name in programs:
        path = shutil.which(name)
        item = {'path': path, 'available': bool(path)}
        if path and name in {'opencode', 'codex'}:
            probe = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=15)
            item.update(available=probe.returncode == 0, version=probe.stdout.strip())
        commands[name] = item
    report = {'capabilities': capabilities, 'python': platform.python_version(), 'platform': platform.system(), 'mode': args.mode,
              'modules': modules, 'commands': commands,
              'ok': sys.version_info >= (3, 11) and all(modules.values())
                    and all(item['available'] for item in commands.values()),
              'notes': ['Page CLI process groups and coordinator locking currently require POSIX.',
                        'Font availability and model/vision capabilities still require task-specific preflight.']}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['ok'] else 1)


if __name__ == '__main__':
    main()
