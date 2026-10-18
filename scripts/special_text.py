"""Native code/grammar rendering, with immutable UTF-8 source assets.

Offsets are Unicode half-open intervals. Generated formatting lives in a second
file, never in the source text. Only renderer-owned commands acquire TeX catcodes.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Callable
try:
    from semantic_contract import color_spans, span_color, validate_grammar_lines
except ImportError:
    from .semantic_contract import color_spans, span_color, validate_grammar_lines


class SpecialTextError(ValueError):
    pass


SPECIAL_TEXT_PREAMBLE = r"""
\usepackage{fvextra}
\usepackage{xcolor}
\usepackage{tikz}
\usetikzlibrary{decorations.pathreplacing}
\providecommand{\BookCodeFontFamily}{tt}
\providecommand{\BookCodeFontSize}{10pt}
\providecommand{\BookCodeBaseline}{14pt}
\fvset{fontsize=\fontsize{\BookCodeFontSize}{\BookCodeBaseline}\selectfont,fontfamily=\BookCodeFontFamily}
\newcommand{\BookCodeUnderline}[1]{\leavevmode\vtop{\offinterlineskip\hbox{#1}\kern0.25ex\hrule height0.35pt}}
\newcommand{\BookCodeAnchor}[1]{\tikz[remember picture,overlay]{\coordinate (#1);}}
\makeatletter
\def\FV@fontfamily@rm{\def\FV@FontFamily{\rmfamily}}
\makeatother
"""

KINDS = {"normal", "underline", "kai", "bold", "italic", "bold_italic", "upright", "monospace", "color"}
COMMANDS = {"underline": "BookCodeUnderline", "bold": "textbf", "italic": "textit", "monospace":"texttt",
            "upright": "textup"}


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _layout(block: dict, key: str, default=None):
    presentation = block.get("code_presentation", {})
    aliases = {"wrap_policy": "wrap", "tab_size": "tabsize"}
    value = presentation.get(aliases.get(key, key), block.get("layout", {}).get(key, block.get(key, default)))
    if key == "wrap_policy" and value in {"source", "preserve"}:
        return "none"
    return value


def _annotations(block: dict) -> list[dict]:
    return block.get("annotations", block.get("code_presentation", {}).get("annotations", []))


def _number(value, name: str, low=0, high=100) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise SpecialTextError(f"{name}: finite value in [{low}, {high}] required")
    return value


def _lines(text: str) -> list[str]:
    # A trailing newline terminates a source line; it is not an extra blank line.
    result = text.split("\n")
    if result and result[-1] == "":
        result.pop()
    return result or [""]


def validate_special_block(block: dict) -> None:
    kind = block.get("type")
    if kind not in {"code", "grammar"}:
        raise SpecialTextError(f"unsupported special type: {kind}")
    if not isinstance(block.get("id"), str) or not block["id"]:
        raise SpecialTextError("special block needs a stable id")
    if not isinstance(block.get("text"), str):
        raise SpecialTextError("special block text must be a string")
    text = block["text"]
    if block.get("text_sha256") not in {None, _sha(text)}:
        raise SpecialTextError(f"{block['id']}: text_sha256 mismatch")
    if any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise SpecialTextError("unsupported source control character; supply explicit display policy")
    if kind == "grammar":
        if 'grammar_lines' in block:
            try:
                validate_grammar_lines(block)
            except ValueError as error:
                raise SpecialTextError(str(error)) from error
        elif not isinstance(block.get("latex", block.get("native_latex")), str) and not isinstance(block.get("alternatives"), list):
            raise SpecialTextError("grammar needs reviewed latex or literal alternatives")
        if block.get("styles"):
            raise SpecialTextError("grammar styles must be encoded in reviewed latex or alternatives; outer ranges are not implemented")
        return
    if block.get("code_presentation", {}).get("font_policy", "native") not in {"source", "inherit", "monospace", "proportional", "native"}:
        raise SpecialTextError("unsupported code font_policy")
    _number(_layout(block, "outer_indent_ccwd", 0), "outer_indent_ccwd", high=20)
    if _layout(block, "wrap_policy", "none") not in {"none", "soft"}:
        raise SpecialTextError("wrap_policy must be none or soft")
    tab_size = _layout(block, "tab_size")
    if "\t" in text and tab_size is None:
        raise SpecialTextError("source tabs need an explicit tab_size")
    if tab_size is not None and (type(tab_size) is not int or not 1 <= tab_size <= 32):
        raise SpecialTextError("tab_size must be an integer from 1 to 32")
    for style in block.get("styles", []):
        if style.get("kind") not in KINDS:
            raise SpecialTextError(f"unsupported code style: {style.get('kind')}")
        start, end = style.get("start"), style.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise SpecialTextError("code style range must be a nonempty Unicode half-open interval")
    try:
        color_spans(block.get('styles', []))
    except ValueError as error:
        raise SpecialTextError(str(error)) from error
    line_count = len(_lines(text))
    source_ids = block.get("source_line_ids")
    if source_ids is not None and (not isinstance(source_ids, list) or len(source_ids) != line_count or len(set(source_ids)) != line_count):
        raise SpecialTextError("source_line_ids must identify every source line uniquely")
    for annotation in _annotations(block):
        start, end = _annotation_lines(block, annotation)
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= line_count:
            raise SpecialTextError("annotation needs an inclusive 1-based source line range")
        if annotation.get("side", "right") != "right":
            raise SpecialTextError("only right-side code brace annotations are implemented")
        if not isinstance(annotation.get("label", annotation.get("label_block_id", annotation.get("label_id"))), str):
            raise SpecialTextError("annotation requires label or label_id")
        if _layout(block, "wrap_policy", "none") != "none":
            raise SpecialTextError("annotated code cannot soft-wrap; source line anchors would be ambiguous")
        _number(annotation.get('x_offset_ccwd', 0), 'annotation x_offset_ccwd', low=-6, high=6)
        _number(annotation.get('start_yshift_em', 0.45), 'annotation start_yshift_em', low=-3, high=3)
        _number(annotation.get('end_yshift_em', -0.35), 'annotation end_yshift_em', low=-3, high=3)
    if _annotations(block):
        _number(_layout(block, "annotation_width_ccwd", 6), "annotation_width_ccwd", low=2, high=30)


def _annotation_lines(block: dict, annotation: dict) -> tuple[int, int]:
    source_ids = block.get("source_line_ids", [])
    result = []
    for key in ("start", "end"):
        value = annotation.get(key + "_line")
        if value is None and key + "_line_id" in annotation:
            try:
                value = source_ids.index(annotation[key + "_line_id"]) + 1
            except ValueError as exc:
                raise SpecialTextError("annotation references unknown source_line_id") from exc
        result.append(value)
    return tuple(result)


def _markers(text: str) -> tuple[str, str, str]:
    # XeTeX accepts private-use Unicode command/group catcodes. Unlike an ASCII
    # escape scheme, this keeps all original backslashes and braces literal.
    found = [chr(cp) for cp in range(0xE000, 0xF8FF + 1) if chr(cp) not in text][:3]
    if len(found) != 3:
        raise SpecialTextError("no collision-free command characters available")
    return tuple(found)


def _decorate(text: str, styles: list[dict], markers: tuple[str, str, str], *, offset=0) -> str:
    escape, opening, closing = markers
    bounds = {offset, offset + len(text)}
    for style in styles:
        for value in (style["start"], style["end"]):
            if offset < value < offset + len(text):
                bounds.add(value)
    result = []
    points = sorted(bounds)
    for start, end in zip(points, points[1:]):
        segment = text[start - offset:end - offset]
        active = sorted({s["kind"] for s in styles if s["kind"] != 'normal' and s["start"] <= start and s["end"] >= end})
        for kind in active:
            if kind == 'color':
                # xcolor's textcolor has a literal brace-delimited parameter;
                # Verbatim's private group characters cannot delimit it. The
                # standard scoped color declaration accepts ordinary arguments.
                segment = (opening + escape + 'color[HTML]' + opening + span_color(styles, start, end)
                           + closing + segment + closing)
            elif kind == 'kai':
                segment = opening + escape + 'kaishu ' + segment + closing
            elif kind == 'bold_italic':
                segment = escape + 'textbf' + opening + escape + 'textit' + opening + segment + closing + closing
            else:
                segment = escape + COMMANDS[kind] + opening + segment + closing
        result.append(segment)
    return "".join(result)


def collect_special_assets(blocks: list[dict], stage: Path) -> dict[str, dict]:
    """Write source/render assets and a hash manifest under the export stage.

    Returned paths are stage-relative, safe names for native VerbatimInput. Input
    blocks are not mutated. Caller packages the whole special-text directory.
    """
    stage = Path(stage)
    output = stage / "special-text"
    output.mkdir(parents=True, exist_ok=True)
    assets = {}
    for block in blocks:
        if block.get("type") not in {"code", "grammar"}:
            continue
        validate_special_block(block)
        identifier, text = block["id"], block["text"]
        if identifier in assets:
            raise SpecialTextError(f"duplicate special block id: {identifier}")
        basename = "block-" + _sha(identifier)[:24]
        source_path = f"special-text/{basename}.source.txt"
        (stage / source_path).write_bytes(text.encode("utf-8"))
        metadata = {"id": identifier, "text_sha256": _sha(text), "source_path": source_path,
                    "source_sha256": _sha(text), "source_file_sha256": _sha(text),
                    "source_line_count": len(_lines(text))}
        if block["type"] == "code":
            markers = _markers(text) if block.get("styles") or _annotations(block) else None
            prefix = "sc" + _sha(identifier)[:20]
            rendered = text
            if markers:
                rendered_lines, offset = [], 0
                for index, source_line in enumerate(text.split("\n")):
                    if index == len(text.split("\n")) - 1 and not source_line and text.endswith("\n"):
                        break
                    line = _decorate(source_line, block.get("styles", []), markers, offset=offset)
                    if _annotations(block):
                        e, o, c = markers
                        line = e + "BookCodeAnchor" + o + prefix + "L" + str(index + 1) + c + line
                    rendered_lines.append(line)
                    offset += len(source_line) + 1
                rendered = "\n".join(rendered_lines) + ("\n" if text.endswith("\n") else "")
            render_path = f"special-text/{basename}.render.txt"
            (stage / render_path).write_bytes(rendered.encode("utf-8"))
            metadata.update(render_path=render_path, render_sha256=_sha(rendered),
                            commandchars="".join(markers) if markers else None,
                            anchor_prefix=prefix,
                            # Logical line indices remain stable; no soft-wrap map is
                            # claimed here. Actual display wrapping needs PDF review.
                            source_line_numbers=list(range(1, len(_lines(text)) + 1)),
                            source_to_display_map=({str(n): [n] for n in range(1, len(_lines(text)) + 1)}
                                                   if _layout(block, "wrap_policy", "none") == "none" else None),
                            display_line_mapping_status=("source_lines_unwrapped" if _layout(block, "wrap_policy", "none") == "none"
                                                         else "needs_pdf_layout_review"))
        assets[identifier] = metadata
    (output / "manifest.json").write_text(json.dumps(assets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return assets


def _escape(text: str) -> str:
    escapes = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "%": r"\%", "&": r"\&",
               "#": r"\#", "_": r"\_", "$": r"\$", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(escapes.get(c, c) for c in text)


def _grammar_literal(text):
    # Ordinary TeX collapses spaces. Explicit native spaces retain the source
    # hierarchy without treating any program character as a TeX instruction.
    return r'\texttt{' + _escape(text).replace('<', r'\textless{}').replace('>', r'\textgreater{}').replace(' ', r'\ ') + '}'


def _render_grammar(block: dict, math_validator=None) -> str:
    latex = block.get("latex", block.get("native_latex"))
    if 'grammar_lines' in block:
        rows = []
        for row in block['grammar_lines']:
            value, cursor = '', 0
            anchor = row.get('align_to')
            if anchor:
                target = block['grammar_lines'][anchor['line']]
                value += r'\phantom{' + _grammar_literal(target['text'][:anchor['offset']]) + '}'
                if target.get('indent_em'):
                    value += r'\hspace*{' + f"{target['indent_em']:g}em" + '}'
                cursor = len(row['text']) - len(row['text'].lstrip(' '))
            if row.get('indent_em'):
                value += r'\hspace*{' + f"{row['indent_em']:g}em" + '}'
            for script in row.get('scripts', []):
                a, b = script['start'], script['end']
                if a < cursor:
                    raise SpecialTextError('grammar script overlaps indentation replaced by alignment')
                value += _grammar_literal(row['text'][cursor:a])
                value += ('^' if script['position'] == 'superscript' else '_') + '{' + _grammar_literal(row['text'][a:b]) + '}'
                cursor = b
            value += _grammar_literal(row['text'][cursor:])
            rows.append(value)
        latex = r'\begin{array}{@{}l@{}}' + r'\\'.join(rows) + r'\end{array}'
    if latex is None:
        rows = []
        for row in block["alternatives"]:
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                raise SpecialTextError("grammar alternatives require literal text rows")
            value = _escape(row["text"])
            if row.get("keyword"):
                value = r"\underline{" + value + "}"
            rows.append(r"\text{\ttfamily " + value + "}")
        if not rows:
            raise SpecialTextError("grammar alternatives cannot be empty")
        left, right = block.get("left_delimiter", "\\{"), block.get("right_delimiter", ".")
        if left not in {r"\{", "[", "(", "."} or right not in {r"\}", "]", ")", "."}:
            raise SpecialTextError("unsupported grammar delimiter")
        latex = r"\left" + left + r"\begin{array}{l}" + r"\\".join(rows) + r"\end{array}\right" + right
    # Independent defense in depth: native grammar accepts only known formatting
    # and math commands, never filesystem/process/macro-definition primitives.
    unsafe = {"input", "include", "openin", "openout", "read", "write", "immediate", "catcode", "csname", "endcsname", "def", "edef", "gdef", "xdef", "let", "newcommand", "renewcommand", "special", "directlua", "usepackage", "documentclass", "includegraphics", "futurelet", "pdfobj", "pdfextension", "everyjob", "loop", "repeat"}
    for command in re.findall(r"\\([A-Za-z]+)", latex):
        if command in unsafe:
            raise SpecialTextError(f"unsafe grammar command: {command}")
    allowed_environments = {"aligned", "alignedat", "gathered", "split", "cases", "array", "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix"}
    for environment in re.findall(r"\\(?:begin|end)\s*\{([^}]+)\}", latex):
        if environment not in allowed_environments:
            raise SpecialTextError(f"unsupported grammar environment: {environment}")
    if math_validator:
        math_validator(latex)
    return "\\[\n" + latex + "\n\\]"


def render_special_block(block: dict, assets: dict[str, dict] | None = None, *,
                         inline_renderer: Callable | None = None, math_validator: Callable | None = None,
                         block_map: dict[str, dict] | None = None) -> str:
    """Render code or grammar; labels may resolve through block_map.

    inline_renderer receives a full label/caption block. If absent, literal
    text is safely escaped.
    """
    validate_special_block(block)
    if block["type"] == "grammar":
        return _render_grammar(block, math_validator)
    if assets is None or block["id"] not in assets:
        raise SpecialTextError("code source assets must be collected before rendering")
    asset = assets[block["id"]]
    if asset.get("text_sha256") != _sha(block["text"]):
        raise SpecialTextError("code assets are stale relative to source text")
    path = asset["render_path"]
    if not re.fullmatch(r"special-text/block-[0-9a-f]+\.render\.txt", path):
        raise SpecialTextError("unsafe generated code asset path")
    indent = _layout(block, "outer_indent_ccwd", 0)
    options = ["baselinestretch=1", "showspaces=false", "showtabs=false",
               f"xleftmargin={indent:g}\\ccwd", "gobble=0", "vspace=0pt"]
    policy = block.get('code_presentation', {}).get('font_policy', 'native')
    if policy in {'monospace','proportional'}:
        options.append('fontfamily=' + {'monospace':'tt','proportional':'rm'}[policy])
    if asset.get("commandchars"):
        options.append("commandchars=" + asset["commandchars"])
    if _layout(block, "tab_size") is not None:
        options.append("tabsize=" + str(_layout(block, "tab_size")))
    if _layout(block, "wrap_policy", "none") == "soft":
        options += ["breaklines=true", "breakautoindent=false", "breaksymbolleft={}", "breaksymbolright={}"]
    commands = [r"\par\addvspace{0.3\baselineskip}", r"\begingroup"]
    annotations = _annotations(block)
    if annotations:
        reserve = _layout(block, "annotation_width_ccwd", 6)
        commands += [r"\noindent\begin{minipage}{\linewidth}",
                     f"\\begin{{minipage}}{{\\dimexpr\\linewidth-{reserve:g}\\ccwd\\relax}}"]
    commands.append("\\VerbatimInput[" + ",".join(options) + "]{" + path + "}")
    if annotations:
        commands.append(r"\end{minipage}")
        prefix = asset["anchor_prefix"]
        for annotation in annotations:
            start, end = _annotation_lines(block, annotation)
            label = {"text": annotation.get("label", "")}
            label_id = annotation.get("label_block_id", annotation.get("label_id"))
            if label_id is not None:
                if block_map is None or label_id not in block_map:
                    raise SpecialTextError("annotation label_id must resolve to a label block")
                label = block_map[label_id]
            text = inline_renderer(label) if inline_renderer else _escape(label["text"])
            offset = annotation.get('x_offset_ccwd', 0)
            start_shift, end_shift = annotation.get('start_yshift_em', 0.45), annotation.get('end_yshift_em', -0.35)
            xshift = f"\\dimexpr\\linewidth-{reserve:g}\\ccwd-{indent:g}\\ccwd+{offset:g}\\ccwd+0.5em\\relax"
            commands.append(r"\begin{tikzpicture}[remember picture,overlay]" + "\n" +
                            "\\draw[decorate,decoration={brace,amplitude=4pt}] " +
                            f"([xshift={{{xshift}}},yshift={start_shift:g}em]{prefix}L{start}) -- " +
                            f"([xshift={{{xshift}}},yshift={end_shift:g}em]{prefix}L{end}) " +
                            f"node[midway,right=0.7em,text width=\\dimexpr{reserve:g}\\ccwd-1.5em\\relax,font=\\small] {{{text}}};\n" +
                            r"\end{tikzpicture}")
        commands.append(r"\end{minipage}")
    commands += [r"\endgroup", r"\par\addvspace{0.3\baselineskip}"]
    return "\n".join(commands)


def compose_code_group(group: dict, block_map: dict[str, dict], assets: dict[str, dict], *,
                       inline_renderer: Callable | None = None, math_validator: Callable | None = None,
                       block_renderer: Callable | None = None) -> str:
    """Render 2–4 native columns, with ordered bodies and optional captions.

    A column chooses either code_id (legacy code-only form) or nonempty
    block_ids (paragraph/list/code/grammar in source order). Non-special bodies
    require block_renderer(block); inline_renderer remains caption-only.
    Ownership is checked before rendering so a callback cannot hide duplicates.
    """
    columns = group.get("columns")
    if not isinstance(columns, list) or not 2 <= len(columns) <= 4:
        raise SpecialTextError("code_group requires 2-4 columns")
    gap = _number(group.get("gap_ccwd", 1), "gap_ccwd", high=6)
    caption_alignment = group.get('caption_alignment', 'natural')
    if caption_alignment not in {'natural', 'bottom'}:
        raise SpecialTextError('caption_alignment must be natural or bottom')
    seen, normalized, weights = set(), [], []
    for column in columns:
        if not isinstance(column, dict) or ('code_id' in column) == ('block_ids' in column):
            raise SpecialTextError('column requires exactly one of code_id or block_ids')
        legacy = 'code_id' in column
        ids = [column['code_id']] if legacy else column['block_ids']
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(ident, str) or not ident for ident in ids)):
            raise SpecialTextError('column block IDs must be a nonempty list of explicit IDs')
        caption_id = column.get('caption_id')
        if 'caption_id' in column and (not isinstance(caption_id, str) or not caption_id):
            raise SpecialTextError('column caption_id must be an explicit ID when supplied')
        for ident in ids:
            if ident not in block_map or block_map[ident].get('type') not in {'paragraph', 'list', 'code', 'grammar'}:
                raise SpecialTextError('column member must resolve to paragraph/list/code/grammar')
            if legacy and block_map[ident].get('type') != 'code':
                raise SpecialTextError('code_id must resolve to a code block')
        if caption_id is not None and (caption_id not in block_map or block_map[caption_id].get('type') != 'caption'):
            raise SpecialTextError('column caption_id must resolve to a caption block')
        members = ids + ([caption_id] if caption_id is not None else [])
        if len(members) != len(set(members)) or seen.intersection(members):
            raise SpecialTextError('column member is consumed more than once')
        seen.update(members)
        normalized.append((ids, caption_id))
        weights.append(_number(column.get('weight', 1), 'column weight', low=0.01, high=100))
    if 'reading_order' in group:
        order = group['reading_order']
        if not isinstance(order, list) or any(not isinstance(x, str) for x in order) or len(order) != len(seen) or set(order) != seen:
            raise SpecialTextError('group reading_order must cover each member exactly once')

    def body(ids):
        rendered = []
        for ident in ids:
            block = block_map[ident]
            if block['type'] in {'code', 'grammar'}:
                rendered.append(render_special_block(block, assets, inline_renderer=inline_renderer,
                    math_validator=math_validator, block_map=block_map))
            elif block_renderer is None:
                raise SpecialTextError('paragraph/list columns require a full block_renderer')
            else:
                rendered.append(block_renderer(block))
        return "\n".join(rendered)

    # Render once: measurement and final placement reuse the same strings.
    bodies = [body(ids) for ids, _ in normalized]
    rendered, box_names = [r'\begingroup'], []
    if caption_alignment == 'bottom':
        suffix = ''.join(chr(97 + int(x, 16)) for x in _sha(json.dumps(columns, sort_keys=True))[:12])
        height = 'BookCodeGroupHeight' + suffix
        rendered += ['\\newdimen\\' + height, '\\' + height + '=0pt']
        for index, (content, weight) in enumerate(zip(bodies, weights)):
            name = 'BookCodeGroupBox' + suffix + chr(97 + index)
            box_names.append(name)
            fraction = weight / sum(weights)
            width = f'\\dimexpr{fraction:.9f}\\linewidth-{(len(columns)-1)*gap*fraction:.9f}\\ccwd\\relax'
            rendered += ['\\newsavebox{\\' + name + '}',
                         '\\begin{lrbox}{\\' + name + '}'+f'\\begin{{minipage}}[t]{{{width}}}',
                         content, r'\end{minipage}\end{lrbox}',
                         '\\ifdim\\dimexpr\\ht\\' + name + '+\\dp\\' + name + '\\relax>\\' + height,
                         '\\' + height + '=\\dimexpr\\ht\\' + name + '+\\dp\\' + name + r'\relax\fi']
    rendered.append(r"\par\noindent\begin{minipage}{\linewidth}")
    for index, ((ids, caption_id), content, weight) in enumerate(zip(normalized, bodies, weights)):
        fraction = weight / sum(weights)
        gap_fraction = (len(columns) - 1) * gap * fraction
        rendered.append(f"\\begin{{minipage}}[t]{{\\dimexpr{fraction:.9f}\\linewidth-{gap_fraction:.9f}\\ccwd\\relax}}\\vspace{{0pt}}")
        if caption_alignment == 'bottom':
            rendered.append('\\begin{minipage}[t][\\' + height + r'][t]{\linewidth}\vspace{0pt}'
                            + '\\usebox{\\' + box_names[index] + r'}\end{minipage}')
        else:
            rendered.append(content)
        if caption_id is not None:
            caption = block_map[caption_id]
            value = inline_renderer(caption) if inline_renderer else _escape(caption['text'])
            rendered.append(r"\par\smallskip{\centering\small " + value + r"\par}")
        rendered.append(r'\end{minipage}%')
        if index < len(columns) - 1:
            rendered.append(f"\\hspace{{{gap:g}\\ccwd}}%")
    rendered.append(r"\end{minipage}\par\endgroup")
    return "\n".join(rendered)
