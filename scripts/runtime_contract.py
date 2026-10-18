"""Content identities for stage-local Python tools and their runtime environment."""
import ast
import importlib.metadata
import platform
import sys
from pathlib import Path
from source_identity import file_hash


def tool_closure(entries, root=None):
    root = Path(root or Path(__file__).parent).resolve()
    pending = [Path(p).resolve() for p in entries]
    found = {}
    while pending:
        path = pending.pop()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Stage tool must be a local Python file')
        name = str(path.relative_to(root))
        if name in found:
            continue
        found[name] = file_hash(path)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = ([x.name for x in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for module in names:
                local = root / (module.split('.')[0] + '.py')
                if local.is_file():
                    pending.append(local.resolve())
    return dict(sorted(found.items()))


def stage_identity(entries, distributions=('Pillow', 'PyMuPDF')):
    versions = {}
    for name in distributions:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {'tools': tool_closure(entries), 'python': platform.python_version(),
            'python_executable_sha256': file_hash(Path(sys.executable).resolve()),
            'platform': platform.platform(), 'distributions': versions,
            'scope': 'local Python import closure and selected distributions; dynamic tools/assets have separate stage manifests'}
