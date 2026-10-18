"""Resolve a template project without imposing a private body-command protocol.

Legacy two-file templates remain supported. New projects explicitly inventory
their entry/resources in template.json; all paths are relative to the project.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil

ENGINES = ('xelatex', 'lualatex', 'pdflatex')
RESERVED = {'main.tex', 'book-config.tex', 'BUILD.txt', '.latexmkrc',
            'export-support.tex', 'export-template.json'}

# Public package interfaces used by the renderer. This does not set a template's
# fonts, headings, margins, page styles, or list appearance.
NATIVE_SUPPORT = r"""
\usepackage{amsmath,amssymb,bm,mathrsfs,cases}
\usepackage{xcolor,graphicx,array,booktabs,multirow,tabularx,adjustbox,float,caption,needspace}
\usepackage[nomath]{cellspace}
\usepackage{arydshln,pdfpages,multicol,enumitem}
\usepackage[normalem]{ulem}
\usepackage{hyperref}
\makeatletter
\@ifpackageloaded{amsthm}{}{\usepackage{amsthm}}
\makeatother
\ifdefined\ccwd\else\newdimen\ccwd\ccwd=1em\fi
\newlength{\BookTableColumnArea}
\setlength{\cellspacetoplimit}{3pt}
\setlength{\cellspacebottomlimit}{3pt}
\AddToHook{env/array/begin}{\ADLactivate}
\AddToHook{env/tabular/begin}{\ADLinactivate}
"""


def load_template(directory):
    root = Path(directory).resolve()
    manifest = root / 'template.json'
    if not manifest.exists():
        files = ['book-template.tex', 'book-style.sty']
        for name in files:
            if not (root / name).is_file():
                raise ValueError('Template missing: ' + str(root / name))
        return dict(root=str(root), entry=files[0], resources=files, engine='xelatex',
                    family='book', legacy=True, placeholder='@@CHAPTER_INPUTS@@')
    data = json.loads(manifest.read_text(encoding='utf8'))
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('template.json schema_version must be 1')
    if set(data) - {'schema_version', 'entry', 'resources', 'engine', 'family', 'placeholder', 'body_extensions'}:
        raise ValueError('Unknown template.json keys')
    if data.get('engine') not in ENGINES or data.get('family') not in ('book', 'article'):
        raise ValueError('Template requires an explicit supported engine and book/article family')
    files = data.get('resources')
    if not isinstance(files, list) or not files or len(set(files)) != len(files):
        raise ValueError('Template resources must be a nonempty unique list')
    for name in [*files, data.get('entry')]:
        if (not isinstance(name, str) or not name or '\\' in name or
                PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or
                str(PurePosixPath(name)) != name):
            raise ValueError('Template resource needs a normalized relative path')
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Template resource missing/outside project: ' + name)
    if data['entry'] not in files:
        raise ValueError('Template entry must be included in resources')
    for name in files:
        if name != data['entry'] and (name in RESERVED or name.split('/')[0] in ('chapters','images','special-text','covers','body-fonts')):
            raise ValueError('Template resource collides with generated output: ' + name)
    placeholder = data.get('placeholder', '@@BODY@@')
    if placeholder not in ('@@BODY@@', '@@CHAPTER_INPUTS@@'):
        raise ValueError('Template placeholder must be @@BODY@@ or @@CHAPTER_INPUTS@@')
    extensions = data.get('body_extensions', [])
    allowed = {'text-glyph-fallback-v1', 'annotated-code-v1'}
    if not isinstance(extensions, list) or any(e not in allowed for e in extensions):
        raise ValueError('Unknown body extension; use the shared documented interface')
    return {**data, 'root':str(root), 'legacy':False, 'placeholder':placeholder}


def template_hashes(project):
    root = Path(project['root'])
    names = project['resources'] + ([] if project['legacy'] else ['template.json'])
    return {name:hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def copy_template_resources(project, destination):
    root, destination = Path(project['root']), Path(destination)
    for name in project['resources']:
        if name == project['entry']:
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)


def recorder_dependencies(recorder, output):
    """Inventory actually read files; distinguish project and TeX environment.

    Included project resources are frozen before execution. The recorder is
    evidence of actual additional dependencies, not a substitute for admission.
    """
    root = Path(output).resolve()
    records = {}
    for line in Path(recorder).read_text(errors='replace').splitlines():
        if not line.startswith('INPUT '):
            continue
        path = Path(line[6:])
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_file() or path.suffix in ('.aux','.toc','.out'):
            continue
        local = path.is_relative_to(root)
        name = str(path.relative_to(root)) if local else str(path)
        records[name] = {'path':name, 'scope':'package' if local else 'environment',
                         'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    return list(records.values())
