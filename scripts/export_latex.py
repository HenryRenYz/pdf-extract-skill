#!/usr/bin/env python3
"""Export semantic blocks and a selected figure registry to a clean XeLaTeX book.

The input remains the source of truth. This renderer does not repair OCR, infer
missing content, select figure candidates, or equate compilation with accuracy.
Body export uses Python's standard library; native cover validation additionally
uses the image preparation/PyMuPDF environment. Compilation uses XeLaTeX.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from html import unescape
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:
    from figure_fallback import validate_fallback
    from math_presentation import render_math_presentation, math_body
    from table_layout import validate_table_layout, horizontal_rules, cell_spec, TableLayoutError
    from inline_labels import validate_inline_label
    from figure_layout import build_layout_plan
    from latex_numbering import build_numbering_plan, slice_title, reference_bindings
    from table_presentation import build_table_plan
    from document_parts import build_part_plan, unnumbered_part
    from list_presentation import render_list_presentation, validate_list_presentation
    from latex_contents import build_contents_plan
    from semantic_contract import validate_semantic_block, STYLES, color_spans, span_color
    from special_text import collect_special_assets, render_special_block, compose_code_group, SPECIAL_TEXT_PREAMBLE
    from format_preflight import load_format_profile, check_template_ready, validate_preflight_scope, render_configuration, RENDER_DEFAULTS
    from presentation_render import presentation, boundary_separator, paragraph_prefix, local_heading, structured_list, validate_fragment_presentations, LIST_PREAMBLE
    from source_identity import source_digest, manifest_source_hash
    from latex_delivery import annotate_main, font_dependencies, build_instructions, latexmkrc
    from latex_template import load_template, template_hashes, copy_template_resources, recorder_dependencies, NATIVE_SUPPORT
    from asset_contract import asset_reference
except ModuleNotFoundError:
    from .figure_fallback import validate_fallback
    from .math_presentation import render_math_presentation, math_body
    from .table_layout import validate_table_layout, horizontal_rules, cell_spec, TableLayoutError
    from .inline_labels import validate_inline_label
    from .figure_layout import build_layout_plan
    from .latex_numbering import build_numbering_plan, slice_title, reference_bindings
    from .table_presentation import build_table_plan
    from .document_parts import build_part_plan, unnumbered_part
    from .list_presentation import render_list_presentation, validate_list_presentation
    from .latex_contents import build_contents_plan
    from .semantic_contract import validate_semantic_block, STYLES, color_spans, span_color
    from .special_text import collect_special_assets, render_special_block, compose_code_group, SPECIAL_TEXT_PREAMBLE
    from .format_preflight import load_format_profile, check_template_ready, validate_preflight_scope, render_configuration, RENDER_DEFAULTS
    from .presentation_render import presentation, boundary_separator, paragraph_prefix, local_heading, structured_list, validate_fragment_presentations, LIST_PREAMBLE
    from .source_identity import source_digest, manifest_source_hash
    from .latex_delivery import annotate_main, font_dependencies, build_instructions, latexmkrc
    from .latex_template import load_template, template_hashes, copy_template_resources, recorder_dependencies, NATIVE_SUPPORT
    from .asset_contract import asset_reference


try:
    from table_data import TableDataError as ExportError, Cell, TableParser, positive_span, table_rows, structured_rows
except ModuleNotFoundError:
    from .table_data import TableDataError as ExportError, Cell, TableParser, positive_span, table_rows, structured_rows


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def escaped_at(text: str, offset: int) -> bool:
    n = 0
    while offset > 0 and text[offset - 1] == "\\":
        offset -= 1
        n += 1
    return bool(n % 2)


def find_token(text: str, token: str, start: int, end: int) -> int:
    pos = text.find(token, start, end)
    while pos >= 0 and escaped_at(text, pos):
        pos = text.find(token, pos + len(token), end)
    return pos


TEXT_ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "%": r"\%",
    "&": r"\&", "#": r"\#", "_": r"\_", "$": r"\$",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    '<':r'\textless{}', '>':r'\textgreater{}',
    # These scan glyphs are commonly absent from text fonts; preserve their meaning.
    "↓": r"\ensuremath{\downarrow}", "↑": r"\ensuremath{\uparrow}",
    "→": r"\ensuremath{\rightarrow}", "←": r"\ensuremath{\leftarrow}",
    "−": r"\ensuremath{-}", "≤": r"\ensuremath{\leq}", "≥": r"\ensuremath{\geq}",
    "□": r"\ensuremath{\square}",
    "⋯": r"\ensuremath{\cdots}", "⋮": r"\ensuremath{\vdots}", "⋱": r"\ensuremath{\ddots}",
}
for _codepoint in [*range(0x2160, 0x2189), *range(0x2460, 0x2500), 0x2032, 0x2033]:
    TEXT_ESCAPES[chr(_codepoint)] = r"\ExtractTextGlyph{" + chr(_codepoint) + "}"


def escape_text(text: str) -> str:
    return "".join(TEXT_ESCAPES.get(char, char) for char in text)


def find_markup_close(text: str, marker: str, start: int, end: int) -> int:
    """A literal exponent/star in a math token cannot close text emphasis."""
    i = start
    while i < end:
        opener = next((m for m in ("$$", r"\[", r"\(", "$")
                       if text.startswith(m, i) and not escaped_at(text, i)), None)
        if opener:
            closer = {"$$": "$$", r"\[": r"\]", r"\(": r"\)", "$": "$"}[opener]
            close = find_token(text, closer, i + len(opener), end)
            if close < 0:
                raise ExportError(f"unclosed math inside emphasis at Unicode offset {i}")
            i = close + len(closer)
            continue
        if (text.startswith(marker, i) and not escaped_at(text, i)
                and i > start and not text[i - 1].isspace()):
            return i
        i += 1
    return -1


def text_style(text: str, kinds: set[str]) -> str:
    if "double_struck" in kinds or "bold_math" in kinds:
        raise ExportError("mathematical style on ordinary text; correct its semantic range")
    if "bold_italic" in kinds:
        kinds = kinds | {"bold", "italic"}
    if "upright" in kinds:
        text = r"\textup{" + text + "}"
    elif "italic" in kinds:
        text = r"\textit{" + text + "}"
    if "bold" in kinds:
        text = r"\textbf{" + text + "}"
    if "kai" in kinds:
        text = r"{\kaishu " + text + "}"
    if "underline" in kinds:
        text = r"\uline{" + text + "}"
    if 'monospace' in kinds:
        text = r'\texttt{' + text + '}'
    return text


MATH_ENVIRONMENTS = {
    "aligned", "alignedat", "gathered", "split", "cases", "array", "matrix",
    "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix",
}
UNSAFE_COMMANDS = {
    "input", "include", "includegraphics", "openin", "openout", "read", "write",
    "immediate", "catcode", "csname", "endcsname", "def", "edef", "gdef", "xdef",
    "let", "futurelet", "newcommand", "renewcommand", "usepackage", "documentclass",
    "special", "directlua", "pdfobj", "pdfextension", "everyjob", "loop", "repeat",
}


def validate_math(text: str) -> None:
    # Defense in depth, not a TeX sandbox. Compile only reviewed semantic input.
    for command in re.findall(r"\\([A-Za-z]+)", text):
        if command in UNSAFE_COMMANDS:
            raise ExportError(f"unsupported executable TeX command in math: \\{command}")
    for env in re.findall(r"\\(?:begin|end)\s*\{([^}]+)\}", text):
        if env not in MATH_ENVIRONMENTS:
            raise ExportError(f"unsupported embedded math environment: {env}")


def math_regions(text: str, bare_formula: bool = False) -> list[dict]:
    """Locate source offsets; never rewrite delimiters or mathematical content."""
    if bare_formula:
        return [{"start": 0, "end": len(text), "body_start": 0, "body_end": len(text)}]
    regions, i = [], 0
    while i < len(text):
        if text.startswith("<!--", i):
            close = text.find("-->", i + 4)
            if close < 0:
                raise ExportError("unclosed HTML comment")
            i = close + 3
            continue
        tag = re.match(r"</?[A-Za-z][^>]*>", text[i:])
        if tag:
            i += len(tag.group())
            continue
        opener = next((m for m in ("$$", r"\[", r"\(", "$")
                       if text.startswith(m, i) and not escaped_at(text, i)), None)
        if opener:
            closer = {"$$": "$$", r"\[": r"\]", r"\(": r"\)", "$": "$"}[opener]
            close = find_token(text, closer, i + len(opener), len(text))
            if close < 0:
                raise ExportError(f"unclosed math delimiter at Unicode offset {i}")
            regions.append({"start": i, "end": close + len(closer),
                            "body_start": i + len(opener), "body_end": close})
            i = close + len(closer)
        else:
            i += 1
    return regions


MATH_FONT_KINDS = {
    "mathrm": {"upright"}, "mathsf": {"upright"}, "mathtt": {"upright"},
    "mathit": {"italic"}, "mathnormal": {"italic"}, "mathbf": {"bold", "upright"},
    "boldsymbol": {"bold_italic", "bold_math"}, "bm": {"bold_italic", "bold_math"},
    "mathbb": {"double_struck"}, "text": set(), "operatorname": {"upright"},
    "mbox": set(), "hbox": set(), "tag": set(), "begin": set(), "end": set(),
}
DEFAULT_ITALIC_SYMBOLS = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta",
    "theta", "vartheta", "iota", "kappa", "varkappa", "lambda", "mu", "nu", "xi",
    "pi", "varpi", "rho", "varrho", "sigma", "varsigma", "tau", "upsilon", "phi",
    "varphi", "chi", "psi", "omega",
}


def math_style_encoding(text: str, start: int, end: int, style: dict, region: dict) -> tuple[bool, str]:
    """Conservative redundancy recognition, not a general TeX interpreter.

    A command-name substring is never a symbol. Unsupported contexts require
    calibration when the supplied style is confirmed or rule-inferred.
    """
    body_start, body_end = region["body_start"], region["body_end"]
    controls = list(re.finditer(r"\\[A-Za-z]+|\\.", text[body_start:body_end]))
    groups = []
    for control in controls:
        name = control.group()[1:]
        if name not in MATH_FONT_KINDS:
            continue
        opening = body_start + control.end()
        while opening < body_end and text[opening].isspace():
            opening += 1
        if opening >= body_end or text[opening] != "{":
            continue
        depth, close = 1, opening + 1
        while close < body_end and depth:
            if not escaped_at(text, close):
                if text[close] == "{":
                    depth += 1
                elif text[close] == "}":
                    depth -= 1
            close += 1
        if depth == 0:
            groups.append({"start": body_start + control.start(), "body_start": opening + 1,
                           "body_end": close - 1, "end": close, "name": name})
    kind = style["kind"]
    for group in groups:
        if start == group["start"] and end == group["end"] and kind in MATH_FONT_KINDS[group["name"]]:
            return True, "existing_math_font_macro_matches"
    for control in controls:
        a, b = body_start + control.start(), body_start + control.end()
        if start < b and end > a:
            if start == a and end == b and control.group()[1:] in DEFAULT_ITALIC_SYMBOLS and kind == "italic":
                # A font-altering parent could change that symbol's shape.
                if not any(g["body_start"] <= start and end <= g["body_end"] for g in groups):
                    return True, "default_lowercase_greek_math_symbol"
            return False, "style_intersects_tex_control_sequence"
    parent_groups = [g for g in groups if g["body_start"] <= start and end <= g["body_end"]]
    if parent_groups:
        parent = min(parent_groups, key=lambda g: g["end"] - g["start"])
        if re.fullmatch(r"[A-Za-z0-9]+", text[start:end]) and kind in MATH_FONT_KINDS[parent["name"]]:
            return True, "existing_math_font_group_matches"
        return False, "candidate_differs_from_or_is_ambiguous_in_math_font_group"
    # Declaration fonts and environment arguments need a real TeX context parser.
    declarations = {"rm", "bf", "it", "sf", "tt", "cal", "mathversion", "begin", "text", "operatorname", "tag"}
    if any(c.group()[1:] in declarations for c in controls):
        return False, "math_context_requires_font_calibration"
    if kind == "italic" and re.fullmatch(r"[A-Za-z]+", text[start:end]):
        return True, "default_ascii_math_letter_italic"
    if kind == "upright" and re.fullmatch(r"[0-9]+", text[start:end]):
        return True, "default_math_digit_upright"
    return False, "math_style_not_encoded_by_existing_latex"


def audit_math_styles(text: str, styles: list[dict], context: dict, deferred: list[dict],
                      redundant: list[dict], bare_formula: bool = False) -> list[dict]:
    # Validate input ranges/kinds before selecting a rendering-only style copy.
    InlineRenderer(text, styles)
    regions = math_regions(text, bare_formula)
    result = []
    for style in styles:
        pieces = [(style["start"], style["end"])]
        for region in regions:
            a, b = region["body_start"], region["body_end"]
            start, end = max(style["start"], a), min(style["end"], b)
            if start >= end:
                continue
            partial = not (style["start"] <= a and style["end"] >= b)
            math_only = region["start"] <= style["start"] and style["end"] <= region["end"]
            if not (partial or math_only):
                continue
            matched, reason = math_style_encoding(text, start, end, style, region)
            record = {**context, "style": dict(style), "style_text": text[style["start"]:style["end"]],
                      "math_overlap": [start, end], "math_range": [a, b],
                      "math_sha256": sha256(text[a:b].encode()).hexdigest(), "reason": reason,
                      "existing_latex_matches_candidate": matched,
                      "rendering_policy": "preserve_existing_latex_without_applying_this_style"}
            evidence = style.get("evidence", "unreviewed")
            if evidence in {"unreviewed", "uncertain"}:
                record["candidate_verified"] = False
                deferred.append(record)
            elif evidence in {"source_checked", "rule_inferred"} and matched:
                record["source_evidence_independently_authenticated_by_exporter"] = False
                redundant.append(record)
            else:
                raise ExportError(f"{context.get('block_id')}: {evidence} partial math style needs calibration: "
                                  f"{reason}, Unicode interval {style['start']}:{style['end']}")
            # Preserve the style's ordinary-text portion, if it crosses a boundary.
            remaining = []
            for x, y in pieces:
                if y <= a or x >= b:
                    remaining.append((x, y))
                else:
                    if x < a:
                        remaining.append((x, a))
                    if y > b:
                        remaining.append((b, y))
            pieces = remaining
        result.extend({**style, "start": a, "end": b} for a, b in pieces if a < b)
    return result


def prepare_render_block(block: dict, deferred: list[dict], redundant: list[dict]) -> dict:
    """Keep semantic input unchanged; deferred candidates live in the sidecar."""
    if block["type"] in {"header", "footer", "code", "grammar"}:
        return dict(block)
    context = {"block_id": block.get("block_id") or block.get("id"), "page": block.get("page"),
               "text_sha256": sha256(block["text"].encode()).hexdigest()}
    bare = block["type"] == "formula" and not block["text"].lstrip().startswith(("$$", r"\[", "$", r"\("))
    rendered = {**block, "styles": audit_math_styles(block["text"], block.get("styles", []),
                                                     context, deferred, redundant, bare)}
    if block["type"] == "table" and block.get("cells"):
        rows = []
        for r, row in enumerate(structured_rows(block["cells"])):
            if not isinstance(row, list):
                raise ExportError("table cells must be a list of rows")
            cells = []
            for c, cell in enumerate(row):
                if isinstance(cell, dict) and cell.get("styles"):
                    cell_context = {**context, "cell": {"row": r, "column": c},
                                    "text_sha256": sha256(cell.get("text", "").encode()).hexdigest()}
                    cell = {**cell, "styles": audit_math_styles(cell.get("text", ""), cell["styles"],
                                                               cell_context, deferred, redundant)}
                cells.append(cell)
            rows.append(cells)
        rendered["cells"] = rows
    return rendered


MATH_TEXT_CONTEXTS = {"text", "textrm", "textnormal", "textup", "textbf", "textit", "textsf", "texttt", "mbox", "hbox"}
MATH_PUNCTUATION_FALLBACKS = {"，", "。", "；", "："}
MATH_TAG_COMMANDS = DEFAULT_ITALIC_SYMBOLS | {
    "prime", "frac", "dfrac", "tfrac", "sqrt", "sum", "prod", "int", "lim",
    "leq", "leqslant", "geq", "geqslant", "approx", "infty", "cdots", "dots",
    "cdot", "times", "pm", "left", "right", "boldsymbol", "bm", "mathit",
    "mathrm", "mathbf", "mathbb", "mathsf", "mathtt", "overline", "bar", "hat", "vec",
    "overrightarrow", "underline", "substack",
}


def tex_group_end(text: str, opening: int) -> int | None:
    depth, close = 1, opening + 1
    while close < len(text) and depth:
        if not escaped_at(text, close):
            if text[close] == "{":
                depth += 1
            elif text[close] == "}":
                depth -= 1
        close += 1
    return close if depth == 0 else None


def math_tag_insertions(value: str, offset: int, context: dict, fallbacks: list[dict]) -> tuple[dict[int, str], list[tuple[int, int]]]:
    """AMS tags are text-mode arguments; preserve mathematical prime placement."""
    insertions, text_ranges = {}, []
    for command in re.finditer(r"\\tag\*?(?![A-Za-z])", value):
        if escaped_at(value, command.start()):
            continue
        opening = command.end()
        while opening < len(value) and value[opening].isspace():
            opening += 1
        if opening >= len(value) or value[opening] != "{":
            continue
        end = tex_group_end(value, opening)
        if end is None:
            raise ExportError("unclosed equation tag argument")
        body_start, body_end = opening + 1, end - 1
        argument = value[body_start:body_end]
        protected = [(r["start"], r["end"]) for r in math_regions(argument)]
        for macro in re.finditer(r"\\(?:ensuremath|text|textrm|textnormal|textbf|textit|textup|mbox|hbox)(?![A-Za-z])", argument):
            op = macro.end()
            while op < len(argument) and argument[op].isspace():
                op += 1
            if op < len(argument) and argument[op] == "{":
                close = tex_group_end(argument, op)
                if close is not None:
                    protected.append((macro.start(), close))
        outside = lambda i: not any(a <= i < b for a, b in protected)
        needs_math = any(c in "^_" and outside(i) and not escaped_at(argument, i)
                         for i, c in enumerate(argument))
        needs_math = needs_math or any(outside(m.start()) and m.group()[1:] in MATH_TAG_COMMANDS
                                      for m in re.finditer(r"\\[A-Za-z]+|\\.", argument))
        if not needs_math:
            # Literal tag labels are already text mode. Protect their ordinary
            # Chinese wording while leaving explicitly delimited inner math visible.
            cuts = [(body_start + r["start"], body_start + r["end"]) for r in math_regions(argument)]
            cursor = body_start
            for a, b in cuts:
                text_ranges.append((cursor, a))
                cursor = b
            text_ranges.append((cursor, body_end))
            continue
        insertions[body_start] = insertions.get(body_start, "") + r"\ensuremath{"
        insertions[body_end] = "}" + insertions.get(body_end, "")
        fallbacks.append({**context, "tag_command": command.group(), "source_argument": argument,
                          "unicode_range": [offset + body_start, offset + body_end],
                          "math_sha256": sha256(value.encode()).hexdigest(),
                          "latex_argument": r"\ensuremath{" + argument + "}",
                          "reason": "ams_tag_text_mode_argument_requires_math_encoding",
                          "source_text_unchanged": True})
    return insertions, text_ranges


def render_math_glyphs(value: str, offset: int, context: dict, fallbacks: list[dict],
                       unsupported: list[dict], tag_fallbacks: list[dict] | None = None) -> str:
    """Encode known bare CJK punctuation as text, without changing source text.

    Existing text-mode arguments are protected. Unknown Unicode math glyphs are
    kept verbatim and diagnosed; never turn a mathematical alphabet into text.
    """
    tag_insertions, protected = math_tag_insertions(value, offset, context, [] if tag_fallbacks is None else tag_fallbacks)
    for control in re.finditer(r"\\[A-Za-z]+|\\.", value):
        if control.group()[1:] not in MATH_TEXT_CONTEXTS:
            continue
        opening = control.end()
        while opening < len(value) and value[opening].isspace():
            opening += 1
        if opening >= len(value) or value[opening] != "{":
            continue
        depth, close = 1, opening + 1
        while close < len(value) and depth:
            if not escaped_at(value, close):
                if value[close] == "{":
                    depth += 1
                elif value[close] == "}":
                    depth -= 1
            close += 1
        if depth == 0:
            protected.append((opening + 1, close - 1))
    for command in re.finditer(r"\\textcircled(?![A-Za-z])\s*\{", value):
        if any(a <= command.start() < b for a, b in protected):
            continue
        opening = command.end() - 1
        depth, close = 1, opening + 1
        while close < len(value) and depth:
            if not escaped_at(value, close):
                if value[close] == '{': depth += 1
                elif value[close] == '}': depth -= 1
            close += 1
        if depth == 0:
            tag_insertions[command.start()] = tag_insertions.get(command.start(), '') + r'\text{'
            tag_insertions[close] = '}' + tag_insertions.get(close, '')
            InlineRenderer.math_command_fallbacks.append({**context,
                'source_range': [offset + command.start(), offset + close],
                'source_command': value[command.start():close],
                'source_math_sha256': sha256(value.encode()).hexdigest(),
                'policy': 'textcircled_requires_text_mode', 'wrapper': r'\text'})
    output = []
    for i, char in enumerate(value):
        review = next((r for r in context.get('review_markers', [])
                       if r['start'] <= offset + i < r['end']), None)
        if review:
            continue
        output.append(tag_insertions.get(i, ""))
        in_text = any(a <= i < b for a, b in protected)
        known_text_glyph = char in TEXT_ESCAPES and ord(char) > 127
        if ord(char) <= 127 or char.isspace() or (in_text and not known_text_glyph):
            output.append(char)
            continue
        record = {**context, "glyph": char, "codepoint": f"U+{ord(char):04X}",
                  "unicode_offset": offset + i, "math_range": [offset, offset + len(value)],
                  "math_sha256": sha256(value.encode()).hexdigest()}
        if known_text_glyph:
            if in_text:
                encoded = TEXT_ESCAPES[char]
            elif TEXT_ESCAPES[char].startswith(r'\ensuremath{'):
                encoded = TEXT_ESCAPES[char][len(r'\ensuremath{'):-1]
            else:
                encoded = r'\text{' + TEXT_ESCAPES[char] + '}'
            record.update(latex=encoded, reason="known_unicode_glyph_native_or_text_font_fallback")
            fallbacks.append(record)
            output.append(encoded)
        elif char in MATH_PUNCTUATION_FALLBACKS:
            encoded = r"\text{" + char + "}"
            record.update(latex=encoded, reason="bare_cjk_punctuation_requires_text_font")
            fallbacks.append(record)
            output.append(encoded)
        else:
            record.update(reason="unicode_math_glyph_has_no_explicit_renderer_support",
                          rendering_policy="preserve_source_glyph_and_warn")
            unsupported.append(record)
            output.append(char)
    output.append(tag_insertions.get(len(value), ""))
    return "".join(output)


def breakable_inline_math(value: str) -> tuple[str, list[int]]:
    """Native optional comma breaks in long inline lists and ordinary tuples.

    Preserve grouped macro arguments, escaped punctuation and left/right boxes.
    The source expression remains immutable; only TeX line-breaking penalties
    are added, never scaling, splitting a command or changing math glyphs.
    """
    depth, scalable, round_depth, square_depth, i, breaks = 0, 0, 0, 0, 0, []
    while i < len(value):
        char = value[i]
        if char == "\\":
            command = re.match(r"\\([A-Za-z]+|.)", value[i:])
            if command:
                name = command.group(1)
                if name == "left":
                    scalable += 1
                elif name == "right":
                    scalable = max(0, scalable - 1)
                i += len(command.group())
                continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == '(':
            round_depth += 1
        elif char == ')':
            round_depth = max(0, round_depth - 1)
        elif char == '[':
            square_depth += 1
        elif char == ']':
            square_depth = max(0, square_depth - 1)
        elif char == "," and depth == 0 and scalable == 0 and square_depth == 0 and value[i + 1:].strip():
            if not value[i + 1:].lstrip().startswith(r"\allowbreak"):
                breaks.append(i + 1)
        i += 1
    if len(value) < 30 and len(breaks) < 2:
        return value, []
    for position in reversed(breaks):
        # Prefer moving a whole expression first; comma breaks are a fallback.
        value = value[:position] + r"\penalty1000 " + value[position:]
    return value, breaks


class InlineRenderer:
    """Lex math first, then parse inline markup and Unicode-index style ranges.

    No replacement pass runs over the resulting TeX or inside math tokens.
    """

    numbering_plan: dict = {}
    document_family = 'book'
    math_line_breaks: list[dict] = []
    math_command_fallbacks: list[dict] = []
    review_annotations: list[dict] = []

    def __init__(self, text: str, styles: list[dict] | None = None,
                 glyph_fallbacks: list[dict] | None = None, unsupported_glyphs: list[dict] | None = None,
                 context: dict | None = None, footnotes: dict[str, str] | None = None,
                 footnote_positions: dict[int, dict] | None = None,
                 math_tag_fallbacks: list[dict] | None = None):
        self.text = text
        self.styles = styles or []
        self.glyph_fallbacks = [] if glyph_fallbacks is None else glyph_fallbacks
        self.unsupported_glyphs = [] if unsupported_glyphs is None else unsupported_glyphs
        self.context = {"text_sha256": sha256(text.encode()).hexdigest(), **(context or {})}
        self.footnotes = footnotes or {}
        self.footnote_positions = footnote_positions or {}
        self.math_tag_fallbacks = [] if math_tag_fallbacks is None else math_tag_fallbacks
        self.footnote_uses: Counter = Counter()
        bindings, _ = reference_bindings(text, self.numbering_plan.get('targets', {}))
        self.reference_positions = {b['start']: b for b in bindings}
        color_spans(self.styles)
        allowed = STYLES
        for style in self.styles:
            a, b = style.get("start"), style.get("end")
            if (not isinstance(a, int) or not isinstance(b, int)
                    or isinstance(a, bool) or isinstance(b, bool) or not 0 <= a < b <= len(text)):
                raise ExportError(f"invalid Unicode style interval: {style}")
            if style.get("kind") not in allowed:
                raise ExportError(f"unknown style kind: {style.get('kind')}")

    def kinds(self, start: int, end: int) -> set[str]:
        return {s["kind"] for s in self.styles if s["start"] <= start and s["end"] >= end}

    def styled(self, value, start, end, active):
        result = text_style(value, self.kinds(start, end) | active)
        color = span_color(self.styles, start, end)
        return r"\textcolor[HTML]{" + color + "}{" + result + "}" if color else result

    def footnote(self, marker: str) -> str:
        self.footnote_uses[marker] += 1
        if self.footnote_uses[marker] > 1:
            raise ExportError(f"footnote marker occurs more than once in its anchor: {marker}")
        return self.footnotes[marker]

    def plain(self, start: int, end: int, active: set[str]) -> str:
        cuts = sorted({start, end} | {v for s in self.styles for v in (s["start"], s["end"])
                                     if start < v < end} |
                      {v for m in re.finditer(r"\n[ \t]*\n+", self.text[start:end])
                       for v in (start + m.start(), start + m.end())})
        return "".join(escape_text(self.text[a:b]) if not self.text[a:b].strip()
                       else self.styled(escape_text(self.text[a:b]), a, b, active)
                       for a, b in zip(cuts, cuts[1:]))

    def math(self, start: int, end: int, active: set[str]) -> str:
        value = self.text[start:end]
        validate_math(value)
        # A style may wrap a complete expression. Partial mathematical ranges must
        # already be represented by math macros; splitting a command corrupts TeX.
        kinds = self.kinds(start, end) | active
        for s in self.styles:
            if s["start"] < end and s["end"] > start and not (s["start"] <= start and s["end"] >= end):
                raise ExportError("partial style inside math; encode that symbol/subexpression with a math macro")
        value = render_math_glyphs(value, start, self.context, self.glyph_fallbacks,
                                  self.unsupported_glyphs, self.math_tag_fallbacks)
        color = span_color(self.styles, start, end)
        if color:
            value = r"{\color[HTML]{" + color + "}" + value + "}"
        if "double_struck" in kinds:
            return r"\mathbb{" + value + "}"
        if "bold_italic" in kinds or "bold_math" in kinds or {"bold", "italic"} <= kinds:
            return r"\boldsymbol{" + value + "}"
        if "bold" in kinds:
            return r"\mathbf{" + value + "}"
        if "italic" in kinds:
            return r"\mathit{" + value + "}"
        if "upright" in kinds:
            return r"\mathrm{" + value + "}"
        return value

    def display(self, start: int, end: int, active: set[str]) -> str:
        records = self.numbering_plan.get('displays', {}).get(self.context.get('block_id'), [])
        record = next((r for r in records if r['content_start'] == start and r['content_end'] == end), {})
        tag = record.get('tag')
        if tag and tag.get('supported'):
            a, b = start + tag['tag_start'], start + tag['tag_end']
            body = self.math(start, a, active) + self.math(b, end, active)
        else:
            body = self.math(start, end, active)
        environment = record.get('environment', 'equation*')
        commands = '\n'.join(record.get('counter_precommands', []))
        label = r'\label{' + record['label'] + '}' if record.get('label') else ''
        return ('\n' + (commands + '\n' if commands else '') + f"\\begin{{{environment}}}\n" + body.strip()
                + (('\n' + record.get('exception_tag_latex', '') + label) if record.get('exception_tag_latex') or label else '')
                + f"\n\\end{{{environment}}}\n")

    def render(self, start: int = 0, end: int | None = None, active: set[str] | None = None) -> str:
        end = len(self.text) if end is None else end
        active = active or set()
        out, i, plain_start = [], start, start

        def flush(pos: int) -> None:
            nonlocal plain_start
            if plain_start < pos:
                out.append(self.plain(plain_start, pos, active))

        while i < end:
            reference = self.reference_positions.get(i)
            if reference and reference['end'] <= end:
                flush(i)
                out.append(self.styled(reference['latex'], i, reference['end'], active))
                i = reference['end']
                plain_start = i
                continue
            binding = self.footnote_positions.get(i)
            if binding:
                if binding["end"] > end:
                    raise ExportError("footnote binding crosses an inline markup boundary")
                flush(i)
                out.append(self.footnote(binding["marker"]))
                i = binding["end"]
                plain_start = i
                continue
            opener = next((x for x in ("$$", r"\[", r"\(", "$")
                           if self.text.startswith(x, i) and not escaped_at(self.text, i)), None)
            if opener:
                closer = {"$$": "$$", r"\[": r"\]", r"\(": r"\)", "$": "$"}[opener]
                close = find_token(self.text, closer, i + len(opener), end)
                if close < 0:
                    raise ExportError(f"unclosed math delimiter at Unicode offset {i}")
                flush(i)
                if opener in ("$$", r"\["):
                    # A display continues its introduction; trailing Markdown
                    # blank lines must not create an empty paragraph before it.
                    while out and not out[-1].strip():
                        out.pop()
                    if out:
                        out[-1] = out[-1].rstrip()
                    out.append(self.display(i + len(opener), close, active))
                else:
                    body = self.math(i + len(opener), close, active)
                    boundary_break = len(body) >= 30
                    body, break_positions = breakable_inline_math(body)
                    if break_positions or boundary_break:
                        self.math_line_breaks.append({**self.context,
                            "source_math_range": [i + len(opener), close],
                            "source_math_sha256": sha256(self.text[i + len(opener):close].encode()).hexdigest(),
                            "rendered_body_break_positions": break_positions,
                            "before_math_boundary": boundary_break,
                            "command": r"\allowbreak", "inner_comma_command": r"\penalty1000",
                            "policy": "long_inline_math_native_break_opportunities"})
                    out.append((r"\allowbreak" if boundary_break else "") + r"\(" + body.strip() + r"\)")
                i = close + len(closer)
                plain_start = i
                continue
            # Backslash escapes belong to Markdown ordinary text only.
            if self.text[i] == "\\" and i + 1 < end and self.text[i + 1] in r"\`*_{}[]()#+-.!$%&":
                flush(i)
                out.append(text_style(escape_text(self.text[i + 1]), self.kinds(i, i + 2) | active))
                i += 2
                plain_start = i
                continue
            marker = next((m for m in ("***", "**", "*") if self.text.startswith(m, i)), None)
            if marker and i + len(marker) < end and not self.text[i + len(marker)].isspace():
                close = find_markup_close(self.text, marker, i + len(marker), end)
                if close > i + len(marker):
                    flush(i)
                    extra = {"bold", "italic"} if marker == "***" else ({"bold"} if marker == "**" else {"italic"})
                    out.append(self.render(i + len(marker), close, active | extra))
                    i = close + len(marker)
                    plain_start = i
                    continue
            # Legacy source anchors/comments are dropped only as parsed tokens.
            if self.text.startswith("<!--", i):
                close = self.text.find("-->", i + 4, end)
                if close < 0:
                    raise ExportError("unclosed HTML comment")
                flush(i)
                i = close + 3
                plain_start = i
                continue
            tag = re.match(r"<(a|b|strong|i|em|u|sup|br)\b[^>]*>", self.text[i:end], re.I)
            if tag:
                name = tag.group(1).lower()
                flush(i)
                if name == "br":
                    out.append(r"\newline{}")
                    i += len(tag.group())
                else:
                    closing = re.search(r"</" + name + r"\s*>", self.text[i + len(tag.group()):end], re.I)
                    if not closing:
                        raise ExportError(f"unclosed inline HTML tag: {name}")
                    close = i + len(tag.group()) + closing.start()
                    extra = {"bold"} if name in ("b", "strong") else ({"italic"} if name in ("i", "em") else set())
                    marker = self.text[i + len(tag.group()):close].strip()
                    if name == "sup" and marker in self.footnotes and not self.footnote_positions:
                        out.append(self.footnote(marker))
                    else:
                        value = self.render(i + len(tag.group()), close, active | extra)
                        command = "underline" if name == "u" else ("textsuperscript" if name == "sup" else None)
                        out.append("\\" + command + "{" + value + "}" if command else value)
                    i = close + len(closing.group())
                plain_start = i
                continue
            # Ordinary hyperlinks retain their visible wording, never source URLs.
            if self.text[i] == "[":
                link = re.match(r"\[([^\]\n]*)\]\([^\)\n]*\)", self.text[i:end])
                if link:
                    flush(i)
                    out.append(self.render(i + 1, i + 1 + len(link.group(1)), active))
                    i += len(link.group())
                    plain_start = i
                    continue
            if self.text.startswith("![", i) or re.match(r"</?(?:img|table|tr|td|div|span|script|style|svg|iframe)(?:\s[^>]*|\s*)>", self.text[i:end], re.I):
                raise ExportError("unsupported inline image/HTML; use semantic figure/table blocks")
            marker = next((m for m in sorted(self.footnotes if not self.footnote_positions else {}, key=len, reverse=True)
                           if self.text.startswith(m, i)), None)
            if marker:
                flush(i)
                out.append(self.footnote(marker))
                i += len(marker)
                plain_start = i
                continue
            i += 1
        flush(end)
        return "".join(out)



def starts_display(tex: str) -> bool:
    """Ignore native counter initializers when finding a display boundary."""
    if tex.lstrip().startswith(r'\['):
        return True
    return bool(re.match(r"\s*(?:(?:\\setcounter\{[^{}]+\}\{[^{}]+\}|\\pushQED\{\\qed\})\s*)*"
                         r"\\begin\{(?:equation\*?|align\*|numcases)\}", tex))


def join_rendered(parts: list[str], blocks: list[dict] | None = None) -> tuple[str, list[int]]:
    """Join semantic units without starting an empty paragraph before math.

    New prose paragraphs still receive a blank-line separator. Return exact
    line offsets for provenance, including footnotes and narrow text columns.
    """
    chunks, offsets, line = [], [], 0
    for index, original in enumerate(parts):
        content = original.strip('\n')
        separator = (boundary_separator(blocks[index + 1], parts[index + 1], starts_display)
                     if blocks and index + 1 < len(parts) else
                     '\n' if index + 1 < len(parts) and starts_display(parts[index + 1]) else '\n\n')
        offsets.append(line)
        chunks.append(content + separator)
        line += content.count('\n') + separator.count('\n')
    return ''.join(chunks), offsets


def render_table(block: dict, glyph_fallbacks: list[dict] | None = None,
                 unsupported_glyphs: list[dict] | None = None,
                 math_tag_fallbacks: list[dict] | None = None,
                 caption: str = "") -> str:
    presentation = block.get('_table_presentation', {})
    caption_position = presentation.get('caption_position', 'top')
    if caption:
        caption = r'\captionsetup{position=' + caption_position + '}\n' + caption
    parts = presentation.get('table_parts', [])
    if parts:
        columns = []
        for part in parts:
            start, end = part['text_range']
            child = {**block, 'text': block['text'][start:end], 'styles': [],
                     'table_layout': part['table_layout'], '_table_presentation': part['presentation']}
            child.pop('cells', None)
            child_tex = render_table(child, glyph_fallbacks, unsupported_glyphs, math_tag_fallbacks)
            child_tex = child_tex.removeprefix(r'\begin{table}[H]').removesuffix(r'\end{table}')
            columns.append(f"\\begin{{minipage}}[t]{{{part['presentation']['width_mm']:.6f}mm}}\\vspace{{0pt}}\n"
                           + child_tex.strip() + '\n' + r'\end{minipage}')
        joined = (f"%\n\\hspace{{{presentation['parts_gap_mm']:.6f}mm}}%\n").join(columns)
        return r'\begin{table}[H]\centering' + '\n' + (caption + '\n' if caption_position == 'top' else '') + joined + '\n' + (caption if caption_position == 'bottom' else '') + '\n' + r'\end{table}'
    rows = table_rows(block)
    if not rows:
        raise ExportError("empty table")
    grid: dict[tuple[int, int], tuple[int, int, Cell]] = {}
    width = 0
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in grid:
                c += 1
            if cell.align not in {"c", "l", "r"} or cell.valign not in {"c", "t", "b"}:
                raise ExportError("unsupported table cell alignment")
            if r + cell.rowspan > len(rows):
                raise ExportError("table rowspan extends beyond its last row")
            for rr in range(r, r + cell.rowspan):
                for cc in range(c, c + cell.colspan):
                    if (rr, cc) in grid:
                        raise ExportError("overlapping table spans")
                    grid[rr, cc] = (r, c, cell)
            c += cell.colspan
        width = max(width, c)
    if not grid:
        raise ExportError("table contains no cells")
    width = max(c + 1 for _, c in grid)
    if width > 256:
        raise ExportError("table too wide")
    try:
        layout = validate_table_layout(block.get('table_layout'), grid, len(rows), width)
        if (layout['text_sha256'] != sha256(block['text'].encode()).hexdigest()
                or layout['source_image_sha256'] != block.get('source_image_sha256')):
            raise TableLayoutError('Table layout source/text hashes differ from current block')
    except TableLayoutError as error:
        raise ExportError(str(error)) from error
    # Shared full-height vertical boundaries belong in the column preamble.
    # Only real spans/row-specific boundaries need multicolumn.
    boundaries = []
    for c in range(width + 1):
        styles = [next((('||' if seg.get('style') == 'double' else '|')
                       for seg in layout['vertical'] if seg['boundary'] == c
                       and seg['start'] <= r < seg['end']), '') for r in range(len(rows))]
        boundaries.append(styles[0] if len(set(styles)) == 1 else '')
    logical_specs = [(boundaries[0] if c == 0 else '') + 'c' + boundaries[c + 1] for c in range(width)]
    presentation = block.get('_table_presentation', {})
    policy = presentation.get('width_policy', 'natural_width')
    columns = presentation.get('column_layout', {})
    alignments = columns.get('column_alignment', ['c'] * width)
    if isinstance(alignments, str):
        alignments = [alignments] * width
    logical_specs = [(boundaries[0] if c == 0 else '') + alignments[c] + boundaries[c + 1] for c in range(width)]
    vertical_review = {(item['row'], item['column']):
                       {'top': 't', 'middle': 'c', 'center': 'c', 'bottom': 'b'}[item['alignment']]
                       for item in columns.get('cell_vertical_alignment_review', [])}
    fixed = policy != 'natural_width'
    auxiliaries = set(columns.get('auxiliary_columns', []))
    fractions = []
    area_setup = ''
    if fixed:
        weights = presentation.get('rendered_adaptation', {}).get(
            'column_width_weights', columns.get('column_width_weights'))
        if not weights or len(weights) != width:
            raise ExportError('Fixed-width table requires source-reviewed column widths')
        fractions = [value / sum(weights) for value in weights]
        # Use template lengths, so changing shared padding/rules does not
        # require Python-side hardcoded millimetre corrections.
        rule_count = sum(b.count('|') for b in boundaries)
        double_count = sum(b == '||' for b in boundaries)
        area_setup = (r'\setlength{\BookTableColumnArea}{\dimexpr '
                      + f"{presentation['width_mm']:.9f}mm"
                      + f"-{rule_count}\\arrayrulewidth-{double_count}\\doublerulesep\\relax" + '}')

    def column_dimension(column, span=1):
        fraction = sum(fractions[column:column + span])
        # A merged cell has ordinary exterior padding. The only removed
        # padding is an internal split belonging to a reviewed arrow column.
        pads = (2 if span > 1 else
                (0 if column in auxiliaries else 1) + (0 if column + 1 in auxiliaries else 1))
        return (r'\dimexpr ' + f'{fraction:.12f}' + r'\BookTableColumnArea-'
                + str(pads) + r'\tabcolsep\relax')

    baseline_grid = presentation.get('row_layout', {}).get('baseline_offsets_em')

    def native_spec(row, column, span=1, align='c'):
        old = cell_spec(layout, row, column, span, align)
        if not fixed:
            return old if baseline_grid is not None else old.replace(align, 'S{' + align + '}', 1)
        left = old[:old.index(align)]
        right = old[old.index(align) + 1:]
        # Multicolumn restores ordinary exterior tabcolsep. Internal physical
        # borders do not cross valid merged cells (validated above).
        declaration = {'c': r'\centering', 'l': r'\raggedright', 'r': r'\raggedleft'}[align]
        return left + '>{' + declaration + r'\arraybackslash}' + (('m{' + column_dimension(column, span) + '}') if baseline_grid is not None else ('S{m{' + column_dimension(column, span) + '}}')) + right

    specs = [native_spec(0, c, align=alignments[c]) for c in range(width)]
    # Preamble uses only boundaries shared by all rows; partial source rules
    # remain cell-specific overrides below.
    if fixed:
        for c in range(width):
            specs[c] = ((boundaries[0] if c == 0 else '') + '>{' + {'c': r'\centering', 'l': r'\raggedright', 'r': r'\raggedleft'}[alignments[c]] + r'\arraybackslash}' + (('m{' + column_dimension(c) + '}') if baseline_grid is not None else ('S{m{' + column_dimension(c) + '}}')) + boundaries[c + 1])
    preamble = ''.join(spec + (r'@{}' if c + 1 in auxiliaries else '') for c, spec in enumerate(specs))
    output = [r"\begin{table}[H]", r"\centering"]
    if caption and caption_position == 'top':
        output.append(caption)
    if area_setup:
        output.append(area_setup)
    table_env = 'tabular'
    if baseline_grid is not None:
        # Override the template's normal row strut only for reviewed baseline
        # grids. Glyphs stay full size; vacant staggered rows may share height.
        output += [r"\begingroup", r"\AddToHookNext{env/tabular/begin}{\renewcommand{\arraystretch}{0}\setlength{\extrarowheight}{0pt}}"]
    output.append(r"\begin{tabular}{" + preamble + "}")
    for r in range(len(rows)):
        output.extend(horizontal_rules(layout, r))
        rendered, c = [], 0
        while c < width:
            value = grid.get((r, c))
            if value is None:
                spec = cell_spec(layout, r, c, align=alignments[c])
                rendered.append('' if spec == logical_specs[c] else f"\\multicolumn{{1}}{{{native_spec(r, c, align=alignments[c])}}}{{}}")
                c += 1
                continue
            origin_r, origin_c, cell = value
            if c != origin_c:
                raise ExportError("invalid table span grid")
            context = {"block_id": block.get("block_id") or block.get("id"), "page": block.get("page"),
                       "cell": {"row": origin_r, "column": origin_c}, "text_basis": "parsed_table_cell"}
            content = (InlineRenderer(cell.text, cell.styles, glyph_fallbacks, unsupported_glyphs, context,
                                      math_tag_fallbacks=math_tag_fallbacks).render()
                       if r == origin_r else "")
            # A <th> role is structural; only explicit styles imply emphasis.
            if cell.rowspan > 1 and r == origin_r:
                valign = vertical_review.get((origin_r, origin_c), cell.valign)
                multirow_width = '=' if fixed else '*'
                if r'\newline{}' in content:
                    stack_align = cell.align if cell.align != 'c' else alignments[c]
                    content = r'\shortstack[' + stack_align + ']{' + content.replace(r'\newline{}', r'\\') + '}'
                if fixed:
                    content = {'c': r'\centering ', 'l': r'\raggedright ',
                               'r': r'\raggedleft '}[cell.align if cell.align != 'c' else alignments[c]] + content
                content = f"\\multirow[{valign}]{{{cell.rowspan}}}{{{multirow_width}}}{{{content}}}"
            if baseline_grid is not None:
                height = (r"\dimexpr\height+.2em\relax" if r == 0 else f"{(baseline_grid[r]-baseline_grid[r-1])/2:.6f}em")
                depth = (r"\dimexpr\depth+.2em\relax" if r == len(rows)-1 else f"{(baseline_grid[r+1]-baseline_grid[r])/2:.6f}em")
                content = r"\raisebox{0pt}[" + height + "][" + depth + "]{" + content + "}"
            alignment = cell.align if cell.align != 'c' else alignments[c]
            spec = cell_spec(layout, r, c, cell.colspan, alignment)
            if cell.colspan != 1 or spec != logical_specs[c]:
                content = f"\\multicolumn{{{cell.colspan}}}{{{native_spec(r, c, cell.colspan, alignment)}}}{{{content}}}"
            rendered.append(content)
            c += cell.colspan
        row_spacing = presentation.get('row_spacing_pt', [0] * len(rows))[r]
        row_end = r" \\" + (f"[{row_spacing:.6f}pt]" if row_spacing else '')
        output.append(" & ".join(rendered) + row_end)
    output.extend(horizontal_rules(layout, len(rows)))
    output.append("\\end{" + table_env + "}")
    if baseline_grid is not None:
        output.append(r"\endgroup")
    if caption and caption_position == 'bottom':
        output.append(caption)
    output.append(r"\end{table}")
    return "\n".join(output)


def load_proof_endings(path, blocks, source_hash):
    if not path:
        return []
    metadata = json.loads(path.read_text())
    if metadata.get('source_sha256') != source_hash:
        raise ExportError('Proof-endings metadata belongs to a different source')
    by_id = {b.get('block_id', b.get('id')): b for b in blocks}
    positions = {bid: i for i, bid in enumerate(by_id)}
    used = set()
    for record in metadata.get('proof_endings', []):
        mark, anchor = record['mark_block_id'], record['anchor_block_id']
        review = record.get('review', {})
        if not review.get('source_checked') or not review.get('reviewer') or not review.get('evidence_path'):
            raise ExportError('QED binding requires actual source review')
        if mark not in by_id or anchor not in by_id or mark in used:
            raise ExportError('Missing/duplicate proof-end binding')
        used.add(mark)
        m, a = by_id[mark], by_id[anchor]
        for block, field in ((m, 'marker_text_sha256'), (a, 'anchor_text_sha256')):
            if record.get(field) != sha256(block['text'].encode()).hexdigest():
                raise ExportError('Stale proof-end text hash')
        if record.get('source_image_sha256') != m.get('source_image_sha256'):
            raise ExportError('Proof-end page image hash mismatch')
        start, end = record['marker_start'], record['marker_end']
        if m['text'][start:end] != record['marker_text'] or record['marker_text'] not in ('□', '$\\square$', r'\square', r'\(\square\)', r'\Box'):
            raise ExportError('Unsupported proof-end marker binding')
        flat_list = a['type'] == 'list' and not a.get('items')
        if (a['type'] not in {'paragraph', 'proof', 'example', 'formula'} and not flat_list) or a.get('footnotes'):
            raise ExportError('QED anchor needs ordinary text, a flat list step, or a display without footnotes')
        if record['kind'] == 'standalone':
            if positions[mark] != positions[anchor] + 1 or m['text'].strip() != record['marker_text']:
                raise ExportError('Standalone QED must immediately follow its complete anchor')
        elif record['kind'] == 'terminal_inline':
            suffix = m['text'][end:].strip()
            display_closer = any(r['body_start'] <= start < end <= r['body_end']
                                 and not m['text'][end:r['body_end']].strip()
                                 and not m['text'][r['end']:].strip()
                                 and m['text'][r['start']:r['body_start']] in ('$$', r'\[')
                                 for r in math_regions(m['text'])) if suffix else False
            if mark != anchor or (suffix and not display_closer):
                raise ExportError('Inline QED must end its paragraph or final display body')
        else:
            raise ExportError('Unknown QED binding kind')
    return metadata.get('proof_endings', [])


def attach_proof_end(rendered: str, *, display: bool = False) -> tuple[str, str]:
    """Use amsthm's native QED stack when the proof ends in a display."""
    if not display:
        return rendered.rstrip() + r'\qed', r'\qed'
    ends = list(re.finditer(r'\\end\{equation\*?\}', rendered))
    if not ends or rendered[ends[-1].end():].strip():
        raise ExportError('Formula QED anchor must end in a native display')
    end = ends[-1]
    starts = list(re.finditer(r'\\begin\{equation\*?\}', rendered[:end.start()]))
    if not starts:
        raise ExportError('Formula QED has no display beginning')
    start = starts[-1].start()
    body = rendered[start:end.start()]
    # A nested aligned box only gives qedhere its own width. For an unnumbered
    # whole display, the native full-width align* places QED on the last row at
    # the text edge without inventing a position macro or changing counters.
    match = re.fullmatch(r'\\begin\{equation\*\}\s*\\begin\{aligned\}(.*?)'
                         r'\\end\{aligned\}\s*', body, re.S)
    if match and not re.search(r'\\(?:tag|label)\b', match.group(1)):
        math_body = match.group(1)
        trailing_break = re.search(r'\\\\\s*$', math_body)
        position = trailing_break.start() if trailing_break else len(math_body.rstrip())
        math_body = math_body[:position] + r'\qedhere' + '\n' + math_body[position:]
        math_body = re.sub(r'\n\s*\n', '\n', math_body).strip('\n')
        return (rendered[:start] + r'\pushQED{\qed}' + '\n' + r'\begin{align*}'
                + '\n' + math_body + '\n' + r'\end{align*}' + '\n' + r'\popQED'), r'\qedhere'
    aligned = list(re.finditer(r'\\end\{(?:aligned|alignedat|gathered)\}', body))
    position = start + aligned[-1].start() if aligned else end.start()
    if aligned:
        trailing_break = re.search(r'\\\\\s*$', rendered[start:position])
        if trailing_break:
            position = start + trailing_break.start()
    rendered = rendered[:position] + r'\qedhere' + '\n' + rendered[position:]
    rendered = rendered[:start] + r'\pushQED{\qed}' + '\n' + rendered[start:]
    return rendered.rstrip() + '\n' + r'\popQED', r'\qedhere'


def native_caption(block: dict, kind: str, glyph_fallbacks=None, unsupported_glyphs=None, math_tag_fallbacks=None) -> str:
    ident = block.get('block_id', block.get('id'))
    record = InlineRenderer.numbering_plan.get('captions', {}).get(ident)
    title = slice_title(block, record) if record else block
    content = InlineRenderer(title['text'], title.get('styles'), glyph_fallbacks,
                             unsupported_glyphs, {'block_id': ident, 'page': block.get('page')},
                             math_tag_fallbacks=math_tag_fallbacks).render()
    if record:
        if record['kind'] != kind:
            raise ExportError('caption kind differs from its semantic owner')
        return ('\n'.join(record['counter_precommands']) + '\n' + r'\caption{' + content + '}'
                + r'\label{' + record['label'] + '}')
    return r'\caption*{' + content + '}' if content else ''


def parse_pages(value: str) -> list[int]:
    pages = set()
    for part in value.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not match:
            raise ExportError(f"invalid page range: {part}")
        a, b = int(match[1]), int(match[2] or match[1])
        if not 1 <= a <= b:
            raise ExportError(f"invalid page range: {part}")
        pages.update(range(a, b + 1))
    return sorted(pages)


def block_pages(block: dict) -> set[int]:
    pages = set(block.get("pages", [])) | {s.get("page") for s in block.get("sources", [])}
    pages.add(block.get("page"))
    pages.discard(None)
    if any(not isinstance(p, int) or isinstance(p, bool) or p < 1 for p in pages):
        raise ExportError("physical page values must be positive integers")
    return pages


def load_registry(path: Path | None, source_hash: str | None = None) -> dict[str, dict]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict) and value.get("source_sha256") and value["source_sha256"] != source_hash:
        raise ExportError("figure registry original source_sha256 disagrees with semantic source")
    figures = value.get("figures", []) if isinstance(value, dict) else value
    if not isinstance(figures, list):
        raise ExportError("figure registry must be a list or an object with figures list")
    out = {}
    for figure in figures:
        ident = figure.get("figure_id") or figure.get("id")
        if not isinstance(ident, str) or not ident or ident in out:
            raise ExportError(f"missing/duplicate registry figure ID: {ident}")
        out[ident] = figure
    return out


def select_figure(figure: dict, base: Path, nominal_mm: float) -> tuple[Path, float, dict]:
    acceptance = figure.get("acceptance") or {}
    status = acceptance.get("status") or figure.get("status")
    accepted = {"accepted", "accepted_for_learning_redraw", "retained_source", "accepted_source"}
    if status not in accepted:
        raise ExportError(f"figure lacks final acceptance/source fallback: {status}")
    canvas = figure.get("canvas") or {}
    canonical = canvas.get("canonical_page_width_mm")
    if canonical is not None and not math.isclose(float(canonical), nominal_mm, rel_tol=1e-6):
        raise ExportError("figure nominal page width disagrees with --nominal-page-mm")
    width = figure.get("display_width_mm", canvas.get("width_mm"))
    if not isinstance(width, (float, int)) or not math.isfinite(width) or not 0 < width <= nominal_mm:
        raise ExportError(f"missing/invalid selected figure display width: {width}")
    candidates: dict[str, tuple[str, str | None]] = {}
    final = figure.get("final_assets") or {}
    explicit = final.get("latex") or figure.get("final_asset")
    if explicit:
        raw, expected = asset_reference(explicit)
        fmt = Path(raw).suffix.lstrip(".").lower()
        if fmt not in {"pdf", "png"}:
            raise ExportError("explicit final LaTeX asset must be PDF or PNG")
        expected = expected or figure.get("final_asset_sha256")
        candidates[fmt] = (raw, expected)
        alternate = figure.get("final_asset")
        if final.get("latex") and alternate:
            alternate_raw = alternate["path"] if isinstance(alternate, dict) else alternate
            if alternate_raw != raw:
                raise ExportError("final_assets.latex and final_asset disagree about the selected version")
    else:
        for fmt in ("pdf", "png"):
            if final.get(fmt):
                item = final[fmt]
                candidates[fmt] = (item["path"], item.get("sha256")) if isinstance(item, dict) else (item, None)
    if not explicit and figure.get("final_relative_asset"):
        item = figure["final_relative_asset"]
        candidates.setdefault(Path(item).suffix.lstrip(".").lower(), (item, figure.get("final_asset_sha256")))
    # figure-final-v1 artifacts are already the chosen attempt's final directory.
    # Never fall back to candidate_relative_assets, source crops, or block hints.
    if not candidates:
        ident = figure.get("id") or figure.get("figure_id")
        for item in figure.get("artifacts", []):
            rel = item.get("path", "")
            if rel in (f"{ident}/render/figure.pdf", f"{ident}/render/figure.png"):
                candidates[Path(rel).suffix[1:]] = (rel, item.get("sha256"))
    for fmt in ("pdf", "png"):
        if fmt not in candidates:
            continue
        raw, expected_hash = candidates[fmt]
        path = (base / raw).resolve()
        if not path.is_file():
            raise ExportError(f"selected final figure file is missing: {path}")
        actual_hash = digest(path)
        if expected_hash and actual_hash != expected_hash:
            raise ExportError(f"selected figure hash mismatch: {path}")
        if status in {'retained_source', 'accepted_source'}:
            try:
                validate_fallback(figure, path)
            except ValueError as error:
                raise ExportError(f"{figure.get('figure_id', figure.get('id'))}: {error}") from error
        return path, float(width), {"status": status, "source_asset": str(path), "sha256": actual_hash,
                                    "source_width_mm": width, "width_fraction_of_textwidth": width / nominal_mm}
    raise ExportError("registry has no selected final PDF or PNG")


def safe_name(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return slug[:80] or "chapter"


def bookmark_title(source: str, rendered: str, force: bool = False) -> str:
    """Hyperref-native math title with a safe, independent text bookmark.

    Formatting macros in the visual title must not expand in PDF strings.
    Unknown math commands stay literal in the bookmark, never invented symbols.
    """
    if not force and not math_regions(source):
        return rendered
    plain = re.sub(r"<!--.*?-->|</?[A-Za-z][^>]*>", "", source, flags=re.S)
    plain = re.sub(r"\*\*(.*?)\*\*", r"\1", plain)
    plain = plain.replace(r"\(", "").replace(r"\)", "").replace(r"\[", "").replace(r"\]", "").replace("$", "")
    plain = re.sub(r"\\(?:mathbf|boldsymbol|bm|mathit|mathrm|mathscr|mathcal|mathbb|mathsf|text|operatorname)(?![A-Za-z])", "", plain)
    plain = plain.replace("{", "").replace("}", "")
    plain = re.sub(r"\s+", " ", unescape(plain)).strip()
    escapes = {char: TEXT_ESCAPES[char] for char in "\\%&#_$~^"}
    safe_plain = ''.join(escapes.get(char, char) for char in plain)
    return r'\texorpdfstring{' + rendered + '}{' + safe_plain + '}'


def uses_style(value, kind):
    """Include styles inside list items, code lines, labels and footnotes."""
    if isinstance(value, dict):
        return (value.get('kind') == kind and 'start' in value and 'end' in value
                or any(uses_style(child, kind) for child in value.values()))
    if isinstance(value, list):
        return any(uses_style(child, kind) for child in value)
    return False


def native_heading_command(depth):
    counters = ('chapter','section','subsection','subsubsection','paragraph','subparagraph')
    if InlineRenderer.document_family == 'article':
        counters = counters[1:]
    if not 1 <= depth <= len(counters):
        raise ExportError('Heading depth is unsupported by the selected document family')
    return counters[depth - 1]


def render_block(block: dict, glyph_fallbacks: list[dict] | None = None,
                 unsupported_glyphs: list[dict] | None = None,
                 footnotes: dict[str, str] | None = None,
                 footnote_positions: dict[int, dict] | None = None,
                 math_tag_fallbacks: list[dict] | None = None,
                 special_assets: dict | None = None, block_map: dict | None = None) -> str:
    kind = block["type"]
    def inline(value):
        return InlineRenderer(value['text'], value.get('styles', []), glyph_fallbacks,
                              unsupported_glyphs, {'block_id': block.get('block_id', block.get('id'))},
                              math_tag_fallbacks=math_tag_fallbacks).render()
    if kind in {'code', 'grammar'}:
        return render_special_block(block, special_assets, inline_renderer=inline,
                                    math_validator=validate_math, block_map=block_map)
    if kind == 'display_group':
        return '% native display group ' + safe_name(block.get('block_id', block.get('id')))
    if kind == 'heading':
        content = local_heading(block, inline)
        if content is not None:
            return content
    if footnotes and kind not in {"heading", "paragraph", "theorem", "proof", "example", "exercise", "caption"}:
        raise ExportError("footnote anchors must be ordinary paragraph-like semantic blocks")
    if kind == 'blank':
        if block.get('text') or block.get('styles'):
            raise ExportError('Blank source page cannot contain text or styles')
        return '% Blank source page; identity preserved in provenance.\n'
    if kind in ("header", "footer"):
        return ""
    if kind == "table":
        return render_table(block, glyph_fallbacks, unsupported_glyphs, math_tag_fallbacks)
    text = block["text"]
    context = {"block_id": block.get("block_id") or block.get("id"), "page": block.get("page")}
    review_markers = []
    if kind == 'formula':
        for marker in re.finditer(r'\\text\{\[待核查[：:]([^\]\n]+)\]\}', text):
            ident = marker.group(1)
            notes = [n for n in block.get('uncertainties', []) if n.get('block_id') == ident]
            if ident not in (block.get('source_block_ids') or [context['block_id']]) or len(notes) != 1:
                raise ExportError('formula review marker lacks one matching source uncertainty')
            if review_markers:
                raise ExportError('multiple formula review markers need explicit annotation grouping')
            note = {**context, 'start': marker.start(), 'end': marker.end(),
                    'source_marker': marker.group(), 'reason': notes[0]['reason'],
                    'text_sha256': sha256(text.encode()).hexdigest(),
                    'policy': 'source_uncertainty_native_footnote', 'source_text_unchanged': True}
            review_markers.append(note)
            InlineRenderer.review_annotations.append(note)
        if review_markers:
            context['review_markers'] = review_markers
    if block.get("_source_offset_base"):
        context.update(text_basis="footnote_body_after_source_marker", source_offset_base=block["_source_offset_base"])
    if kind == 'formula' and block.get('numbered_system'):
        records = InlineRenderer.numbering_plan.get('displays', {}).get(context['block_id'], [])
        if len(records) != len(block['numbered_system']['rows']) or any(not r.get('numbered_system') for r in records):
            raise ExportError('numbered system is missing validated native row targets')
        if any(r.get('counter_precommands') for r in records[1:]):
            raise ExportError('numbered system cannot reset counters within native numcases')
        renderer = InlineRenderer(text, block.get('styles', []), glyph_fallbacks,
                                  unsupported_glyphs, context, math_tag_fallbacks=math_tag_fallbacks)
        rows = [renderer.math(r['content_start'], r['content_end'], set()).strip()
                + r'\phantomsection\label{' + r['label'] + '}' for r in records]
        return ('\n'.join(records[0]['counter_precommands']) + '\n'
                + r'\begin{numcases}{}' + '\n'
                + (' ' + r'\\' + '\n').join(rows) + '\n' + r'\end{numcases}')
    if kind == "heading":
        def heading_content(value, styles, offset=0):
            positions = {}
            for start, binding in (footnote_positions or {}).items():
                if not offset <= start < binding['end'] <= offset + len(value):
                    raise ExportError('Heading footnote marker crosses stripped title boundary')
                positions[start-offset] = {**binding, 'end': binding['end']-offset}
            renderer = InlineRenderer(value, styles, glyph_fallbacks, unsupported_glyphs,
                                      context, footnotes, positions, math_tag_fallbacks)
            content = renderer.render()
            if footnotes and set(renderer.footnote_uses) != set(footnotes):
                raise ExportError('Heading footnote marker has no visible binding')
            return bookmark_title(value, content, force=bool(footnotes))
        record = InlineRenderer.numbering_plan.get('headings', {}).get(context['block_id'])
        if record:
            title = slice_title(block, record)
            content = heading_content(title['text'], title.get('styles'), title['_source_offset_base'])
            # Body heading emphasis does not imply bold directory entries.
            # Use the native short-title argument; ToC fonts belong to titletoc.
            toc_content = bookmark_title(title['text'], InlineRenderer(
                title['text'], [], glyph_fallbacks, unsupported_glyphs, context,
                math_tag_fallbacks=math_tag_fallbacks).render())
            return ('\n'.join(record['counter_precommands']) + '\n' + '\\' + record['command']
                    + '[{' + toc_content + '}]{' + content + '}' + r'\label{' + record['label'] + '}')
        # Remove a legacy Markdown heading prefix before assigning style offsets.
        prefix = re.match(r"^#{1,6}\s+", text)
        if prefix:
            offset = len(prefix.group())
            styles = [{**s, "start": s["start"] - offset, "end": s["end"] - offset}
                      for s in block.get("styles", []) if s["start"] >= offset]
            text = text[offset:]
            context.update(text_basis="heading_after_markdown_prefix", source_offset_base=offset)
        else:
            styles = block.get("styles", [])
        content = heading_content(text, styles, len(prefix.group()) if prefix else 0)
        depth = block.get("heading_depth", 2)
        if not isinstance(depth, int) or not 1 <= depth <= 6:
            raise ExportError("heading_depth must be an integer from 1 through 6")
        if str(block.get("chapter_id", "")).startswith("back-"):
            command = native_heading_command(depth)
            result = f"\\{command}*{{{content}}}"
            part = block.get('document_part')
            if depth == 1 and part:
                # Standard starred chapters need an explicit ToC/bookmark entry.
                # Keep a heading footnote out of the moving ToC argument.
                title = escape_text(part['title'])
                result += (r'\label{part:'
                           + block['chapter_id'] + '}'
                           + r'\addcontentsline{toc}{' + native_heading_command(1) + '}{' + title + '}')
            return result
        if str(block.get("chapter_id", "")).startswith("front-"):
            return f"\\{'section' if depth == 1 else 'subsection'}*{{{content}}}"
        command = native_heading_command(depth)
        # Text contains faithful printed numbering. No new counters are generated.
        return f"\\{command}*{{{content}}}"
    if kind not in {"paragraph", "formula", "caption", "theorem", "proof", "example", "exercise", "list"}:
        raise ExportError(f"unsupported semantic block type: {kind}")
    if kind == "formula" and not text.lstrip().startswith(("$$", r"\[", "$", r"\(")) and not math_regions(text):
        validate_math(text)
        return InlineRenderer(text, block.get("styles", []), glyph_fallbacks,
                              unsupported_glyphs, context, math_tag_fallbacks=math_tag_fallbacks).display(0, len(text), set())
    # Explicit list items keep native list environments. Plain legacy list text
    # retains its printed bullets/numbers and therefore adds no guessed numbering.
    if kind == "list" and block.get("items"):
        return structured_list(block, inline)
    renderer = InlineRenderer(text, block.get("styles", []), glyph_fallbacks,
                              unsupported_glyphs, context, footnotes, footnote_positions, math_tag_fallbacks)
    try:
        binding = validate_inline_label(block)
    except ValueError as error:
        raise ExportError(str(error)) from error
    gap = (r"\hspace{\ccwd}" if binding and binding['gap_ccwd'] == 1 else
           (f"\\hspace{{{binding['gap_ccwd']:g}\\ccwd}}" if binding else ''))
    content = (renderer.render(0, binding['end']) + gap
               + renderer.render(binding['separator_end']) if binding else renderer.render())
    if footnotes and set(renderer.footnote_uses) != set(footnotes):
        missing = sorted(set(footnotes) - set(renderer.footnote_uses))
        raise ExportError(f"footnote marker has no visible binding in its anchor: {missing}")
    if kind == "caption":
        return r"\begin{center}" + content + r"\end{center}"
    for note in review_markers:
        content += '\n' + r'\noindent{\footnotesize Textual note\footnote{' + escape_text(
            f"Original PDF page {note['page']}: {note['reason']}") + r'}}\par' + '\n'
    return paragraph_prefix(block, render_list_presentation(block, content))


def collect_footnotes(blocks: list[dict], source_hash: str) -> tuple[dict, dict]:
    definitions, children = {}, {}
    top_ids = {b.get("block_id") or b.get("id") for b in blocks}
    for anchor in blocks:
        anchor_id = anchor.get("block_id") or anchor.get("id")
        notes = anchor.get("footnotes", [])
        if not isinstance(notes, list):
            raise ExportError(f"{anchor_id}: footnotes must be a list")
        markers, positioned = set(), []
        for note in notes:
            marker = note.get("marker") if isinstance(note, dict) else None
            if not isinstance(marker, str) or not marker or marker in markers:
                raise ExportError(f"{anchor_id}: footnotes need distinct nonempty printed markers")
            markers.add(marker)
            if not note.get("review"):
                raise ExportError(f"{anchor_id}: footnote association requires a nonempty reviewed decision record")
            if note.get("anchor_block_id") not in (None, anchor_id):
                raise ExportError(f"{anchor_id}: footnote anchor_block_id disagrees")
            start, end = note.get("marker_start"), note.get("marker_end")
            if start is not None or end is not None:
                if (not isinstance(start, int) or not isinstance(end, int) or isinstance(start, bool)
                        or isinstance(end, bool) or not 0 <= start < end <= len(anchor["text"])):
                    raise ExportError(f"{anchor_id}: invalid Unicode footnote marker interval")
                literal = anchor["text"][start:end]
                if note.get("marker_source_text", literal) != literal:
                    raise ExportError(f"{anchor_id}: footnote marker_source_text differs from source interval")
                if literal != marker and not re.fullmatch(r"<sup\b[^>]*>\s*" + re.escape(marker) + r"\s*</sup\s*>", literal, re.I):
                    raise ExportError(f"{anchor_id}: marker interval is not the reviewed printed marker/sup tag")
                positioned.append((start, end))
            body = note.get("blocks")
            if not isinstance(body, list) or not body:
                raise ExportError(f"{anchor_id}: footnote requires original semantic child blocks")
            for child in body:
                if not isinstance(child, dict) or not isinstance(child.get("text"), str):
                    raise ExportError(f"{anchor_id}: malformed footnote child")
                ident = child.get("block_id") or child.get("id")
                if not isinstance(ident, str) or not ident or ident in top_ids or ident in children:
                    raise ExportError(f"{anchor_id}: duplicate/top-level footnote child ID: {ident}")
                if child.get("source_sha256") != source_hash or any(
                        s.get("source_sha256", source_hash) != source_hash for s in child.get("sources", [])):
                    raise ExportError(f"{ident}: footnote source hash disagrees with book source")
                if child.get("footnotes"):
                    raise ExportError(f"{ident}: nested footnotes need an explicit separate rendering decision")
                if child.get("type") not in {"paragraph", "formula", "table", "theorem", "proof", "example", "exercise", "list", "caption"}:
                    raise ExportError(f"{ident}: unsupported footnote child type: {child.get('type')}")
                children[ident] = child
            definitions.setdefault(anchor_id, []).append(note)
        if positioned and len(positioned) != len(notes):
            raise ExportError(f"{anchor_id}: positioned and unpositioned footnote bindings cannot be mixed")
        for (a, b), (c, d) in zip(sorted(positioned), sorted(positioned)[1:]):
            if b > c:
                raise ExportError(f"{anchor_id}: overlapping footnote marker intervals")
    return definitions, children


def render_footnote(note: dict, anchor_id: str, render_by_id: dict, glyph_fallbacks: list,
                    unsupported_glyphs: list, math_tag_fallbacks: list | None = None) -> dict:
    marker = note["marker"]
    intro = r"\begingroup\renewcommand{\thefootnote}{" + escape_text(marker) + "}\\footnote{\n"
    chunks, mappings, line_offset, removed = [], [], intro.count("\n"), None
    for index, original in enumerate(note["blocks"]):
        ident = original.get("block_id") or original.get("id")
        block = dict(render_by_id[ident])
        if index == 0:
            prefix = re.match(r"^\s*(?:" + re.escape(marker) + r"|<sup\b[^>]*>\s*" + re.escape(marker)
                              + r"\s*</sup\s*>)[ \t\u3000]*", block["text"], re.I)
            if prefix:
                offset = prefix.end()
                removed = {"block_id": ident, "unicode_range": [0, offset], "removed_rendering_prefix": prefix.group(),
                           "reason": "printed_marker_is_already_supplied_by_native_footnote", "source_text_unchanged": True}
                block["text"] = block["text"][offset:]
                block["styles"] = [{**s, "start": max(offset, s["start"]) - offset, "end": s["end"] - offset}
                                   for s in block.get("styles", []) if s["end"] > offset]
                block["_source_offset_base"] = offset
        content = render_block(block, glyph_fallbacks, unsupported_glyphs, math_tag_fallbacks=math_tag_fallbacks)
        mappings.append({"block_id": ident})
        chunks.append(content)
    body, offsets = join_rendered(chunks, [render_by_id[c['block_id']] for c in mappings])
    for mapping, offset in zip(mappings, offsets):
        mapping['line_offset'] = intro.count('\n') + offset
    return {"marker": marker, "anchor_block_id": anchor_id, "review": note["review"],
            "marker_start": note.get("marker_start"), "marker_end": note.get("marker_end"),
            "marker_source_text": note.get("marker_source_text"),
            "latex": intro + body + "}\\endgroup{}", "children": mappings,
            "heading_mark_latex": r'\begingroup\renewcommand{\thefootnote}{' + escape_text(marker)
                                  + r'}\protect\footnotemark\endgroup{}',
            "heading_text_latex": r'\begingroup\renewcommand{\thefootnote}{' + escape_text(marker)
                                  + '}\\footnotetext{\n' + body + r'}\endgroup{}',
            "rendering_prefix_removal": removed, "source_evidence_independently_authenticated_by_exporter": False}


def keep_chapter_end_marks(chapters: list, by_id: dict, registry: dict, nominal_mm: float) -> tuple[list[dict], dict]:
    """Keep only explicit small chapter-end marks with a short adjacent paragraph.

    Instructional figures and ordinary chapter text are never selected by size
    alone. This changes TeX grouping, not semantic order/content/figure scale.
    """
    decisions, line_offsets = [], {}
    for index, (chapter_id, items) in enumerate(chapters[:-1]):
        next_items = chapters[index + 1][1]
        if len(items) < 2 or not next_items or unnumbered_part(chapter_id):
            continue
        next_block = by_id[next_items[0][0]]
        if next_block["type"] != "heading" or next_block.get("heading_depth") != 1:
            continue
        figure_id, figure_tex = items[-1]
        previous_id, previous_tex = items[-2]
        figure, previous = by_id[figure_id], by_id[previous_id]
        flat_list = previous['type'] == 'list' and not previous.get('items')
        if figure["type"] != "figure" or (previous["type"] not in {"paragraph", "exercise", "example", "proof"} and not flat_list):
            continue
        if previous.get("footnotes") or len(previous["text"]) > 320:
            continue
        if any(previous["text"][r["start"]:r["body_start"]] in ("$$", r"\[") for r in math_regions(previous["text"])):
            continue
        if not block_pages(previous) or max(block_pages(previous)) != figure.get("page"):
            continue
        selected = registry.get(figure.get("figure_id"), {})
        canvas = selected.get("canvas") or {}
        width = selected.get("display_width_mm", canvas.get("width_mm"))
        height = selected.get("display_height_mm", canvas.get("height_mm"))
        if (not isinstance(width, (int, float)) or not isinstance(height, (int, float))
                or not 0 < width <= nominal_mm * 0.20 or not 0 < height <= nominal_mm * 0.30):
            continue
        caption_id = figure.get("caption_block_id")
        source_caption = by_id[caption_id]["text"] if caption_id in by_id else selected.get("source_caption", "")
        role = selected.get("layout_role") or selected.get("classification")
        explicit_role = role in {"chapter_end_mark", "chapter_end_qr", "qr_code", "barcode", "decorative_chapter_end"}
        if not explicit_role and not re.search(r"二维码|QR\s*code|条(?:形)?码|barcode", source_caption, re.I):
            continue
        figure_tex = figure_tex.removeprefix(r'\begin{figure}[H]').removesuffix(r'\end{figure}')
        figure_tex = figure_tex.replace(r'\caption{', r'\captionof{figure}{').replace(r'\caption*{', r'\captionof*{figure}{')
        items[-2] = (previous_id, r"\par\noindent\begin{minipage}{\textwidth}\setlength{\parindent}{2em}" + "\n" + previous_tex)
        items[-1] = (figure_id, r"\par\medskip" + figure_tex + "\n" + r"\end{minipage}\par")
        line_offsets[previous_id] = 1
        decisions.append({"chapter_id": chapter_id, "previous_block_id": previous_id,
                          "figure_block_id": figure_id, "figure_id": figure.get("figure_id"),
                          "caption_block_id": caption_id, "source_caption": source_caption,
                          "source_size_mm": [width, height], "previous_text_characters": len(previous["text"]),
                          "reason": "explicit_small_chapter_end_mark_before_next_chapter",
                          "source_order_text_scale_and_ids_unchanged": True,
                          "height_guard": "native_minipage_requires_overfull_and_visual_audit"})
    return decisions, line_offsets


def compose_source_layout(group: dict, rendered: dict[str, str], block_map: dict | None = None) -> str:
    """Standard non-floating minipages; each source body is emitted once.

    The group is reviewed before rendering. Oversized groups must be removed
    from the plan after actual PDF QA, never hidden in duplicate TeX branches.
    """
    def column(width, content, alignment="t"):
        return (f"\\begin{{minipage}}[{alignment}]{{{width:.6f}mm}}\\vspace{{0pt}}\n"
                + content + "\n\\end{minipage}")

    columns = []
    for figure in group['figures']:
        value = rendered[figure['block_id']]
        if not value.startswith(r'\begin{figure}[H]'):
            raise ExportError('Layout figure must use the native figure renderer')
        value = value.removeprefix(r'\begin{figure}[H]').removesuffix(r'\end{figure}')
        value = re.sub(r'(\\includegraphics\[width=)[^,\]]+',
                       lambda match:match[1] + r'\linewidth', value, count=1)
        value = value.replace(r'\caption{', r'\captionof{figure}{').replace(r'\caption*{', r'\captionof*{figure}{')
        columns.append(column(figure['width_mm'], value, 'b' if group['kind'] == 'figure_row' else 't'))
    if group['kind'] == 'figure_row':
        joined = columns[0]
        for gap, content in zip(group['gap_widths_mm'], columns[1:]):
            joined += f"%\n\\hspace{{{gap:.6f}mm}}%\n" + content
        return r'\begin{figure}[H]\centering' + '\n' + joined + '\n' + r'\end{figure}'
    text_ids = group.get('text_block_ids') or [group['text_block_id']]
    body, _ = join_rendered([rendered[bid] for bid in text_ids],
                            [block_map[bid] for bid in text_ids] if block_map else None)
    text = column(group['text_width_mm'], r'\setlength{\parindent}{2em}' + '\n' + body)
    ordered = [columns[0], text] if group['figure_side'] == 'left' else [text, columns[0]]
    return (r'\par\medskip\noindent' + '\n' + r'\begin{minipage}{\textwidth}' + '\n'
            + ordered[0] + f"%\n\\hspace{{{group['gap_width_mm']:.6f}mm}}%\n" + ordered[1]
            + '\n' + r'\end{minipage}\par\medskip')


def compose_text_table(group: dict, rendered: dict[str, str], block_map: dict | None = None) -> str:
    table = rendered[group['table_block_id']]
    if not table.startswith(r'\begin{table}[H]'):
        raise ExportError('Text/table layout requires the native table renderer')
    table = table.removeprefix(r'\begin{table}[H]').removesuffix(r'\end{table}')
    table = table.replace(r'\caption{', r'\captionof{table}{').replace(r'\caption*{', r'\captionof*{table}{')
    def column(width, content):
        return (f"\\begin{{minipage}}[t]{{{width:.6f}mm}}\\vspace{{0pt}}\n"
                + content + '\n' + r'\end{minipage}')
    left = column(group['table_width_mm'], table)
    body, _ = join_rendered([rendered[bid] for bid in group['text_block_ids']],
                            [block_map[bid] for bid in group['text_block_ids']] if block_map else None)
    right = column(group['text_width_mm'], r'\setlength{\parindent}{2em}\noindent ' + body)
    ordered = [left, right] if group['table_side'] == 'left' else [right, left]
    return (r'\par\medskip\noindent\begin{minipage}{\textwidth}' + '\n'
            + ordered[0] + f"%\n\\hspace{{{group['gap_width_mm']:.6f}mm}}%\n" + ordered[1]
            + '\n' + r'\end{minipage}\par\medskip')


def export(args: argparse.Namespace) -> dict:
    export_started = time.perf_counter()
    InlineRenderer.math_line_breaks = []
    InlineRenderer.math_command_fallbacks = []
    blocks_path = args.blocks.resolve()
    figures_path = args.figures.resolve() if args.figures else None
    output = args.output.resolve()
    sidecar = (args.sidecar or output.parent / (output.name + "-export.json")).resolve()
    build = (args.build_dir or output.parent / (output.name + "-build")).resolve()
    if output == Path(output.anchor) or output == blocks_path.parent or output in blocks_path.parents:
        raise ExportError("output cannot be a filesystem root or an ancestor of semantic input")
    inventory_path = getattr(args, 'figure_inventory', None)
    math_path = getattr(args, 'math_presentations', None)
    input_paths = [blocks_path] + [p.resolve() for p in (args.figures, args.manifest, args.source_pdf, args.covers, args.figure_layouts, args.proof_endings, args.numbering_overrides, args.table_presentations, math_path, inventory_path) if p]
    for path in input_paths:
        if path == output or output in path.parents:
            raise ExportError("output cannot contain any input file, even with --force")
        if path == sidecar:
            raise ExportError("sidecar cannot overwrite an input file")
    for path, name in ((sidecar, "sidecar"), (build, "build directory")):
        if path == output or output in path.parents:
            raise ExportError(f"{name} must be outside the final output package")
    if output.exists() and not args.force:
        raise ExportError(f"output already exists (choose a new path or explicitly use --force): {output}")
    if sidecar.exists() and not args.force:
        raise ExportError(f"sidecar already exists: {sidecar}")
    if args.compile and build.exists() and not args.force:
        raise ExportError(f"build directory already exists: {build}")
    if args.compile_timeout <= 0:
        raise ExportError("compile timeout must be positive")
    blocks = []
    for line_number, line in enumerate(blocks_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            block = json.loads(line)
        except json.JSONDecodeError as error:
            raise ExportError(f"invalid JSONL line {line_number}: {error}") from error
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            raise ExportError(f"line {line_number}: block requires text string")
        blocks.append(block)
    if not blocks:
        raise ExportError("no semantic blocks")
    if any(not isinstance(b.get("type"), str) for b in blocks):
        raise ExportError("every semantic block requires a type string")
    ids = [b.get("block_id") or b.get("id") for b in blocks]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ExportError("semantic blocks require unique nonempty IDs")
    by_id = dict(zip(ids, blocks))
    actual_pages = sorted(set().union(*(block_pages(b) for b in blocks)))
    expected_pages = parse_pages(args.expected_pages)
    if actual_pages != expected_pages:
        raise ExportError(f"semantic physical-page coverage differs: expected {expected_pages}, actual {actual_pages}")
    hashes = {b.get("source_sha256") for b in blocks}
    if None in hashes or any(not re.fullmatch(r"[0-9a-f]{64}", h or "") for h in hashes) or len(hashes) != 1:
        raise ExportError("every semantic block must carry the same valid source_sha256")
    source_hash = hashes.pop()
    for block in blocks:
        errors = validate_semantic_block(block, source_hash)
        if errors:
            raise ExportError(f"{block.get('block_id', block.get('id'))}: {errors}")
        if block.get('merged_presentations'):
            fragments = block.get('source_semantics', [])
            if not fragments:
                raise ExportError('Merged presentations require immutable source semantics')
            for key, decision in block['merged_presentations'].items():
                if decision != fragments[0].get('fields', {}).get(key):
                    raise ExportError('Merged presentation differs from its immutable first source fragment')
        for field in ('paragraph_presentation', 'heading_presentation', 'list_presentation',
                      'code_presentation', 'grammar_presentation', 'flow_presentation', 'group_presentation'):
            presentation(block, field)
    profile_path = getattr(args, 'format_profile', None)
    if not profile_path:
        raise ExportError('Export requires --format-profile; use --preflight-mode for a small template sample')
    profile = load_format_profile(profile_path, source_hash)
    configured_render = render_configuration(profile)
    for name, value in configured_render.items():
        override = getattr(args, name, None)
        if override is not None and override != value and not getattr(args, 'preflight_mode', False):
            raise ExportError('Render option differs from frozen profile: ' + name)
        setattr(args, name, value if override is None else override)
    for name in ("nominal_page_mm", "text_width_mm", "paper_width_mm", "paper_height_mm", "figure_scale"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            raise ExportError(f"{name} must be a positive finite number")
    if args.text_width_mm >= args.paper_width_mm or args.paper_height_mm <= 44:
        raise ExportError("paper dimensions leave no usable page margins")
    if getattr(args, 'preflight_mode', False):
        validate_preflight_scope(actual_pages)
        format_admission = {'status': 'preflight', 'production': False, 'profile_sha256': profile['profile_sha256']}
    else:
        ready_path = getattr(args, 'template_ready', None)
        if not ready_path:
            raise ExportError('Production export requires --template-ready')
        format_admission = {**check_template_ready(profile_path, ready_path, source_hash), 'production': True}
    profile_template = Path(profile['template_dir'])
    if args.template_dir and args.template_dir.resolve() != profile_template:
        raise ExportError('--template-dir differs from the source-bound format profile')
    args.template_dir = profile_template
    project = profile['template_project']
    if not project['legacy'] and args.engine != project['engine']:
        raise ExportError('Engine override differs from template.json; revise the template record first')
    footnote_definitions, footnote_children = collect_footnotes(blocks, source_hash)
    all_by_id = {**by_id, **footnote_children}
    all_blocks = blocks + list(footnote_children.values())
    for block in all_blocks:
        errors = validate_semantic_block(block, source_hash)
        if errors:
            raise ExportError(f"{block.get('block_id', block.get('id'))}: {errors}")
        validate_fragment_presentations(block, source_hash)
        for field in ('paragraph_presentation', 'heading_presentation', 'list_presentation',
                      'code_presentation', 'grammar_presentation', 'flow_presentation', 'group_presentation'):
            presentation(block, field)
        if block['type'] == 'display_group' and block.get('group_presentation') is None:
            raise ExportError('Native display_group requires a source-reviewed group_presentation')
    if sorted(set().union(*(block_pages(b) for b in all_blocks))) != expected_pages:
        raise ExportError("footnote child physical pages differ from the declared export scope")
    if any(s.get("source_sha256", source_hash) != source_hash for b in blocks for s in b.get("sources", [])):
        raise ExportError("merged source references disagree with original source_sha256")
    if args.source_pdf and source_digest(args.source_pdf.resolve()) != source_hash:
        raise ExportError("--source-pdf hash differs from semantic source_sha256 (use the original PDF, not a subset)")
    inputs = {"blocks": {"path": str(blocks_path), "sha256": digest(blocks_path)}}
    if args.source_pdf:
        inputs["source_pdf"] = {"path": str(args.source_pdf.resolve()), "sha256": source_hash}
    if figures_path:
        inputs["figures"] = {"path": str(figures_path), "sha256": digest(figures_path)}
    if args.manifest:
        manifest_path = args.manifest.resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_pages = sorted(p["page"] for p in manifest.get("pages", []))
        if not set(expected_pages) <= set(manifest_pages):
            raise ExportError("prepared manifest does not cover the declared export pages")
        source = manifest.get("source") or {}
        manifest_hash = manifest_source_hash(manifest)
        # Subset PDFs have their own hash. The original hash may appear explicitly.
        original_hash = (manifest.get("original_source_sha256") or source.get("original_pdf_sha256")
                         or manifest.get("source_pdf_sha256"))
        if original_hash and original_hash != source_hash:
            raise ExportError("original source hash in prepared manifest disagrees with semantic source")
        working_hashes = {b.get("working_pdf_sha256") for b in blocks}
        if manifest_hash and manifest_hash != source_hash and working_hashes != {manifest_hash}:
            raise ExportError("prepared PDF hash matches neither original nor declared working PDF")
        if manifest.get("preparation_status") not in (None, "complete"):
            raise ExportError("prepared manifest is not complete")
        if manifest.get("preparation_status") == "complete":
            if (sorted(manifest.get("requested_pages", [])) != manifest_pages
                    or sorted(manifest.get("completed_pages", [])) != manifest_pages):
                raise ExportError("prepared manifest page/status sets disagree")
        inputs["manifest"] = {"path": str(manifest_path), "sha256": digest(manifest_path),
                              "prepared_source_sha256": manifest_hash,
                              "original_source_hash_verified": original_hash == source_hash or manifest_hash == source_hash}
    numbering_overrides = json.loads(args.numbering_overrides.read_text()) if args.numbering_overrides else None
    heading_blocks = [b for b in blocks if b.get('heading_presentation', {}).get('role') not in {'local_heading', 'algorithm_label'}]
    InlineRenderer.document_family = project['family']
    numbering_plan = build_numbering_plan(heading_blocks, numbering_overrides, family=project['family'])
    try:
        contents_plan = build_contents_plan(heading_blocks, numbering_plan)
    except ValueError as error:
        raise ExportError(str(error)) from error
    part_plan = build_part_plan(blocks, source_hash)
    list_presentations = [{"block_id": b.get('block_id', b.get('id')),
                           "presentation": validate_list_presentation(b)}
                          for b in blocks if b.get('list_presentation') is not None and not b.get('items')]
    if args.numbering_overrides:
        inputs['numbering_overrides'] = {'path': str(args.numbering_overrides.resolve()), 'sha256': digest(args.numbering_overrides)}
    InlineRenderer.numbering_plan = numbering_plan
    InlineRenderer.review_annotations = []
    proof_decisions = load_proof_endings(args.proof_endings, blocks, source_hash)
    proof_by_anchor = {r['anchor_block_id']: r for r in proof_decisions}
    proof_consumed = {r['mark_block_id'] for r in proof_decisions if r['kind'] == 'standalone'}
    if args.proof_endings:
        inputs['proof_endings'] = {'path': str(args.proof_endings.resolve()), 'sha256': digest(args.proof_endings)}
    registry = load_registry(figures_path, source_hash)
    covers = {}
    if args.covers:
        from prepare_covers import load_cover_manifest
        covers = load_cover_manifest(args.covers, source_hash, expected_pages)
        inputs['covers'] = {'path': str(args.covers.resolve()), 'sha256': digest(args.covers)}
    cover_members = {page: [ident for ident, b in by_id.items() if b.get('page') == page] for page in covers}
    for page, members in cover_members.items():
        if not members or any(block_pages(by_id[ident]) != {page} for ident in members):
            raise ExportError('Retained cover cannot cross a merged body page')
    layout_plan = {'groups': [], 'by_anchor': {}, 'consumed_ids': [], 'warnings': []}
    if args.figure_layouts:
        layout_plan = build_layout_plan(blocks, json.loads(args.figure_layouts.read_text()), text_width_mm=args.text_width_mm, figure_scale=args.figure_scale)
        if any(by_id[i].get('page') in covers for i in layout_plan['consumed_ids']):
            raise ExportError('Whole-page cover and body figure layout cannot consume the same source block')
        inputs['figure_layouts'] = {'path': str(args.figure_layouts.resolve()), 'sha256': digest(args.figure_layouts)}
    table_plan = {'presentations': {}, 'groups': [], 'by_anchor': {}, 'consumed_ids': []}
    if args.table_presentations:
        table_plan = build_table_plan(blocks, json.loads(args.table_presentations.read_text()), text_width_mm=args.text_width_mm)
        if set(table_plan['consumed_ids']) & set(layout_plan['consumed_ids']):
            raise ExportError('Table and figure arrangements cannot consume the same source block')
        inputs['table_presentations'] = {'path': str(args.table_presentations.resolve()), 'sha256': digest(args.table_presentations)}
    elif any(b['type'] == 'table' for b in blocks):
        raise ExportError('Tables require source-reviewed --table-presentations for width and text placement')
    figure_base = (args.figure_root or (figures_path.parent if figures_path else blocks_path.parent)).resolve()
    warnings = []
    figure_coverage = {'coverage_pass':False, 'status':'not_evaluated'}
    if inventory_path:
        if not args.source_pdf:
            raise ExportError('Figure inventory validation requires the original --source')
        try:
            from audit_figures import audit as audit_figure_coverage
        except ImportError:
            from .audit_figures import audit as audit_figure_coverage
        figure_coverage = audit_figure_coverage(args.source_pdf, inventory_path, blocks_path, figures_path, args.covers)
        if sorted(figure_coverage['scope_pages']) != expected_pages:
            raise ExportError('Source figure inventory must cover the exact exported source scope')
        if not figure_coverage['coverage_pass']:
            raise ExportError('Source figure inventory failed: ' + '; '.join(figure_coverage['errors']))
        inputs['figure_inventory'] = {'path':str(inventory_path.resolve()), 'sha256':digest(inventory_path)}
    elif not getattr(args, 'preflight_mode', False):
        raise ExportError('Production export requires --figure-inventory, including reviewed pages without figures')
    else:
        warnings.append('Independent source figure coverage not evaluated; preflight remains needs_review.')
    warnings.extend(str(item) for item in layout_plan['warnings'])
    if args.manifest and manifest.get("preparation_status") is None:
        warnings.append("Historical prepared manifest has no completion status; only its explicit page set/hash was checked.")
    selected: dict[str, dict] = {}
    selected_covers, render_units, rendered_parts = {}, {}, {}
    ignored = []
    deferred_math_styles, redundant_math_styles = [], []
    math_glyph_fallbacks, unsupported_math_glyphs = [], []
    math_tag_fallbacks = []
    render_by_id = {}
    for ident, block in all_by_id.items():
        try:
            render_by_id[ident] = (block if block.get('page') in covers or block['type'] in {'code', 'grammar'} else
                                  prepare_render_block(block, deferred_math_styles, redundant_math_styles))
        except ExportError as error:
            raise ExportError(f"{ident} (physical page {block.get('page')}): {error}") from error
    output.parent.mkdir(parents=True, exist_ok=True)
    special_workspace = tempfile.TemporaryDirectory(prefix='special-text-', dir=output.parent)
    code_defaults = profile['supported_template_rules']['code']
    for ident, b in render_by_id.items():
        if b['type'] == 'code' and 'outer_indent_ccwd' not in b.get('code_presentation', {}):
            render_by_id[ident] = {**b, 'code_presentation': {**b.get('code_presentation', {}),
                'outer_indent_ccwd': code_defaults.get('outer_indent_ccwd', 0)}}
    special_blocks = [{**b, 'id': ident} for ident, b in render_by_id.items() if b['type'] in {'code', 'grammar'}]
    special_assets = collect_special_assets(special_blocks, Path(special_workspace.name)) if special_blocks else {}
    render_by_id = {ident: {**b, 'id': ident} for ident, b in render_by_id.items()}
    annotation_owners = {}
    for ident, block in render_by_id.items():
        if block['type'] != 'code':
            continue
        annotations = block.get('annotations', block.get('code_presentation', {}).get('annotations', []))
        for annotation in annotations:
            label_id = annotation.get('label_block_id', annotation.get('label_id'))
            if label_id is None:
                continue
            if (label_id not in by_id or label_id == ident or label_id in annotation_owners
                    or by_id[label_id]['type'] not in {'paragraph', 'caption'}
                    or by_id[label_id].get('chapter_id') != block.get('chapter_id')
                    or by_id[label_id].get('footnotes')):
                raise ExportError(f'{ident}: annotation label must have one explicit same-chapter text owner')
            annotation_owners[label_id] = ident
            render_units.setdefault(ident, [ident]).append(label_id)
    chapters: list[tuple[str, list[tuple[str, str]]]] = []
    caption_owners = {}
    for block, ident in zip(blocks, ids):
        caption_id = block.get("caption_block_id") if block["type"] in {"figure", "table"} else None
        if not caption_id:
            continue
        if caption_id not in by_id:
            raise ExportError(f"{ident}: caption_block_id does not exist: {caption_id}")
        if caption_id in caption_owners or caption_id == ident:
            raise ExportError(f"{ident}: a caption block cannot belong to multiple figures/tables or itself")
        caption = by_id[caption_id]
        if caption["type"] not in {"paragraph", "caption"}:
            raise ExportError(f"{ident}: caption_block_id must reference caption/paragraph text")
        if caption.get("chapter_id") != block.get("chapter_id"):
            raise ExportError(f"{ident}: caption belongs to a different chapter")
        caption_owners[caption_id] = ident
    attached_captions = {owner: cap for cap, owner in caption_owners.items()}
    if set(annotation_owners) & (set(caption_owners) | set(layout_plan['consumed_ids']) | set(table_plan['consumed_ids'])):
        raise ExportError('Annotation text cannot also belong to an image/table layout')
    if set(footnote_definitions) & set(caption_owners):
        raise ExportError("footnotes on image/table-caption blocks need an explicit separate rendering decision")
    rendered_footnotes = {}
    for anchor_id, notes in footnote_definitions.items():
        rendered_footnotes[anchor_id] = [render_footnote(note, anchor_id, render_by_id,
                                                      math_glyph_fallbacks, unsupported_math_glyphs,
                                                      math_tag_fallbacks) for note in notes]
    # Only contiguous chapter runs may be merged. Do not reorder reading order.
    for block, ident in zip(blocks, ids):
        block = render_by_id[ident]
        if ident in annotation_owners:
            continue
        if ident in contents_plan['source_block_ids']:
            if ident == contents_plan['source_block_ids'][0]:
                chapters.append(('front-contents', [(ident, r'\tableofcontents')]))
                render_units[ident] = contents_plan['source_block_ids']
                rendered_parts[ident] = r'\tableofcontents'
            continue
        if ident in table_plan['presentations']:
            block = {**block, '_table_presentation': table_plan['presentations'][ident]}
        if ident in proof_consumed:
            continue
        proof_ending = proof_by_anchor.get(ident)
        if proof_ending and proof_ending['kind'] == 'terminal_inline':
            marker_start, marker_end = proof_ending['marker_start'], proof_ending['marker_end']
            def after_marker_removal(position):
                return position if position <= marker_start else (marker_start if position < marker_end
                                                                 else position - (marker_end - marker_start))
            styles = [{**style, 'start': after_marker_removal(style['start']),
                       'end': after_marker_removal(style['end'])} for style in block.get('styles', [])]
            block = {**block, 'text': block['text'][:marker_start] + block['text'][marker_end:],
                     'styles': [style for style in styles if style['start'] < style['end']]}
        chapter = str(block.get("chapter_id") or "unassigned")
        if not chapters or chapters[-1][0] != chapter:
            chapters.append((chapter, []))
        try:
            if block.get('page') in covers:
                page = block['page']
                if ident != cover_members[page][0]:
                    continue
                cover = covers[page]
                if cover['mode'] == 'image':
                    asset = Path(cover['asset_path'])
                    if asset == output or output in asset.parents:
                        raise ExportError('Output cannot overwrite selected cover asset')
                    package_path = f"covers/p{page:04d}-{cover['role']}.pdf"
                    rendered = (r'\includepdf[pages=1,pagecommand={\thispagestyle{empty}},'
                                r'width=\paperwidth,height=\paperheight,keepaspectratio]{' + package_path + '}')
                    selected_covers[page] = {**cover, 'package_path': package_path}
                else:
                    meta = cover['metadata']
                    values = [meta.get(k, {}).get('value', '') for k in ('title', 'authors', 'edition', 'publisher')]
                    if meta.get('subtitle'):
                        values[0] += '\n' + meta['subtitle']['value']
                    if meta.get('year'):
                        values[3] += ' ' + meta['year']['value']
                    rendered = (r'\begin{titlepage}\centering' + '\n' + r'\vspace*{0.18\textheight}'
                                + r'{\Huge\sffamily\mdseries ' + escape_text(values[0]) + r'\par}'
                                + ''.join(r'\vspace{8mm}{\large ' + escape_text(v) + r'\par}' for v in values[1:])
                                + r'\end{titlepage}')
                    selected_covers[page] = {**cover, 'package_path': None}
                chapters[-1][1].append((ident, rendered))
                render_units[ident] = cover_members[page]
                rendered_parts[ident] = rendered
                continue
            if ident in caption_owners:
                # Its text is rendered with the owner; it keeps a separate source
                # mapping below and is not an omitted/ignored semantic block.
                continue
            if block["type"] == "figure":
                figure_id = block.get("figure_id")
                if figure_id not in registry:
                    raise ExportError(f"figure block has no final registry entry: {figure_id}")
                if figure_id not in selected:
                    path, width, record = select_figure(registry[figure_id], figure_base, args.nominal_page_mm)
                    if path == output or output in path.parents:
                        raise ExportError("output cannot contain selected input figure assets")
                    fig_source = (registry[figure_id].get("source") or {}).get("pdf_sha256")
                    if fig_source and fig_source != source_hash:
                        raise ExportError("selected figure original source hash disagrees with semantic source")
                    name = "figure-" + sha256(figure_id.encode()).hexdigest()[:12] + path.suffix.lower()
                    selected[figure_id] = {**record, "path": path, "package_path": f"images/{name}"}
                entry = selected[figure_id]
                caption_id = block.get("caption_block_id")
                caption_content = ""
                if caption_id:
                    caption = render_by_id[caption_id]
                    caption_content = native_caption(caption, 'figure', math_glyph_fallbacks,
                                                     unsupported_math_glyphs, math_tag_fallbacks)
                else:
                    caption = block.get("source_caption") or registry[figure_id].get("source_caption")
                    if caption:
                        caption_content = InlineRenderer(caption, [], math_glyph_fallbacks, unsupported_math_glyphs,
                                                         {"block_id": ident, "page": block.get("page"),
                                                          "text_basis": "source_caption_field"},
                                                         math_tag_fallbacks=math_tag_fallbacks).render()
                # Native figure/caption, with shared template scale and source
                # numbering. Unnumbered source illustrations stay unnumbered.
                requested = entry['width_fraction_of_textwidth'] * args.text_width_mm * args.figure_scale
                actual = min(requested, args.text_width_mm)
                entry.update(requested_width_mm=requested, actual_width_mm=actual,
                             width_fit_factor=actual / requested,
                             width_policy='source_scale_capped_at_textwidth',
                             effective_figure_scale=actual / entry['source_width_mm'])
                width_option = f'{min(requested / args.text_width_mm, 1):.10f}' + r'\linewidth'
                if not caption_id and caption_content:
                    caption_content = r'\caption*{' + caption_content + '}'
                rendered = (r'\begin{figure}[H]' + '\n' + r'\centering' + '\n'
                            + f"\\includegraphics[width={width_option},keepaspectratio]"
                            + '{' + entry['package_path'] + '}\n' + caption_content + '\n' + r'\end{figure}')
            else:
                notes = rendered_footnotes.get(ident, [])
                bodies = {note["marker"]: note["heading_mark_latex"] if block['type'] == 'heading'
                          else note["latex"] for note in notes}
                positions = {note["marker_start"]: {"end": note["marker_end"], "marker": note["marker"]}
                             for note in notes if note["marker_start"] is not None}
                if block["type"] == "table":
                    if bodies:
                        raise ExportError("table footnote anchors need an explicit separate rendering decision")
                    caption_id = block.get("caption_block_id")
                    caption_content = ""
                    if caption_id:
                        caption = render_by_id[caption_id]
                        caption_content = native_caption(caption, 'table', math_glyph_fallbacks,
                                                         unsupported_math_glyphs, math_tag_fallbacks)
                    rendered = render_table(block, math_glyph_fallbacks, unsupported_math_glyphs,
                                            math_tag_fallbacks, caption_content)
                else:
                    rendered = render_block(block, math_glyph_fallbacks, unsupported_math_glyphs, bodies, positions,
                                            math_tag_fallbacks, special_assets, render_by_id)
                    if block['type'] == 'heading':
                        rendered += ''.join('\n' + note['heading_text_latex'] for note in notes)
            if proof_ending:
                ends_in_display = bool(re.search(r'\\end\{equation\*?\}\s*$', rendered))
                rendered, qed_command = attach_proof_end(
                    rendered, display=block['type'] == 'formula' or ends_in_display)
                if proof_ending['kind'] == 'standalone':
                    render_units[ident] = [ident, proof_ending['mark_block_id']]
                    rendered_parts[proof_ending['mark_block_id']] = qed_command
            if rendered:
                chapters[-1][1].append((ident, rendered))
                rendered_parts[ident] = rendered
            else:
                ignored.append({"block_id": ident, "type": block["type"]})
        except ExportError as error:
            raise ExportError(f"{ident} (physical page {block.get('page')}): {error}") from error
    unreviewed = Counter(b.get("evidence", "unreviewed") for b in all_blocks)
    if any(k != "source_checked" and v for k, v in unreviewed.items()):
        warnings.append("Content evidence is partly unreviewed/uncertain; export/compilation does not certify OCR accuracy.")
    if any(s.get("evidence") != "source_checked" for b in all_blocks for s in b.get("styles", [])):
        warnings.append("Some typography evidence is unreviewed or rule-inferred; visual source verification remains necessary.")
    if any(b.get("hierarchy_evidence") == "structure_inferred" for b in blocks):
        warnings.append("Heading hierarchy is inferred in input. Native counters are matched to printed source numbers; review actual hierarchy.")
    if deferred_math_styles:
        warnings.append(f"{len(deferred_math_styles)} unreviewed/uncertain mathematical style overlaps were deferred "
                        "to the provenance sidecar; existing math LaTeX is preserved, candidates are not verified.")
    if unsupported_math_glyphs:
        warnings.append(f"{len(unsupported_math_glyphs)} Unicode math glyph positions have no explicit renderer support; "
                        "the original glyphs were preserved. Review unsupported_math_unicode_characters and actual PDF glyph coverage.")
    # Starred source headings need explicit native entries; their titles and
    # page destinations come from the body, not the OCR directory transcript.
    for _, items in chapters:
        for index, (ident, content) in enumerate(items):
            entry = contents_plan['supplemental_headings'].get(ident)
            if not entry:
                continue
            block = render_by_id[ident]
            title = block['text']
            if entry['optional_number']:
                match = re.match(r'^\*\s*' + re.escape(entry['optional_number']) + r'\s+', title)
                title = title[match.end():]
                label = r'\protect\llap{*\space}' + entry['optional_number']
                title = r'\protect\numberline{' + label + '}' + escape_text(title)
            else:
                title = escape_text(title)
            content += (r'\label{' + entry['label'] + '}'
                        + r'\addcontentsline{toc}{' + entry['command'] + '}{' + title + '}')
            items[index] = (ident, content)
            rendered_parts[ident] = content
    # Native code comparisons consume each independently sourced member once.
    code_groups, code_consumed = {}, set()
    for block in blocks:
        if block['type'] != 'display_group':
            continue
        ident = block.get('block_id', block.get('id'))
        groups = block.get('groups', [])
        if not groups:
            raise ExportError(f'{ident}: display_group requires explicit groups')
        consumed_members, rendered_groups = [], []
        for group in groups:
            if 'columns' not in group:
                code_ids, caption_ids = group.get('block_ids', []), group.get('caption_block_ids', [])
                if len(code_ids) != len(caption_ids) or not code_ids:
                    raise ExportError(f'{ident}: native comparison requires paired code/caption IDs')
                weights = group.get('column_weights', [1] * len(code_ids))
                if len(weights) != len(code_ids):
                    raise ExportError(f'{ident}: column weights differ from code columns')
                group = {**group, 'columns': [{'code_id': c, 'caption_id': t, 'weight': w}
                                            for c, t, w in zip(code_ids, caption_ids, weights)]}
            member_ids = [member for column in group['columns']
                          for member in (column.get('block_ids', [column.get('code_id')])
                                         + ([column['caption_id']] if 'caption_id' in column else []))]
            if 'reading_order' in group and Counter(group['reading_order']) != Counter(member_ids):
                raise ExportError(f'{ident}: code group reading_order must cover each member once')
            if ident in member_ids or len(set(member_ids)) != len(member_ids):
                raise ExportError(f'{ident}: duplicate or self-consuming group')
            if any(m not in by_id or by_id[m].get('chapter_id') != block.get('chapter_id') for m in member_ids):
                raise ExportError(f'{ident}: group member missing or belongs to a different chapter')
            if set(member_ids) & (code_consumed | set(layout_plan['consumed_ids']) | set(table_plan['consumed_ids']) | set(caption_owners) | set(annotation_owners)):
                raise ExportError(f'{ident}: group member has multiple owners')
            rendered_groups.append(compose_code_group(group, render_by_id, special_assets,
                inline_renderer=lambda b: InlineRenderer(b['text'], b.get('styles', []),
                    math_glyph_fallbacks, unsupported_math_glyphs,
                    {'block_id': b.get('id', ident)}, math_tag_fallbacks=math_tag_fallbacks).render(),
                block_renderer=lambda b: rendered_parts[b['id']], math_validator=validate_math))
            code_consumed.update(member_ids)
            for member_id in member_ids:
                consumed_members.extend(render_units.get(member_id, [member_id]))
        code_groups[ident] = '\n'.join(rendered_groups)
        render_units[ident] = [ident] + consumed_members
    # Reviewed mathematics uses existing contiguous members as its owner group.
    # Numbered displays and external style spans require a richer mapping; refuse
    # them here rather than silently dropping native references or decorations.
    math_groups = {}
    if math_path:
        decisions = json.loads(math_path.read_text(encoding='utf-8'))
        if decisions.get('source_sha256') != source_hash or not isinstance(decisions.get('groups'), list):
            raise ExportError('Mathematics presentation source/groups mismatch')
        inputs['math_presentations'] = {'path': str(math_path.resolve()), 'sha256': digest(math_path)}
        for decision in decisions['groups']:
            members = decision.get('block_ids', [])
            if not members or any(m not in by_id for m in members):
                raise ExportError('Mathematics group requires existing member IDs')
            owner = members[0]
            occupied = (set(code_groups) | code_consumed | set(layout_plan['consumed_ids'])
                        | set(table_plan['consumed_ids']) | set(caption_owners) | set(annotation_owners))
            if set(members) & occupied:
                raise ExportError('Mathematics group overlaps another layout owner')
            chapter_members = next((items for _, items in chapters if any(i == owner for i, _ in items)), [])
            order = [i for i, _ in chapter_members]
            start = order.index(owner) if owner in order else -1
            if start < 0 or order[start:start + len(members)] != members:
                raise ExportError('Mathematics members must be contiguous in source order')
            for member in members:
                b = by_id[member]
                body, _, _ = math_body(b)
                validate_math(body)
                if (b.get('styles') or b.get('footnotes') or r'\tag' in body
                        or any(record.get('tag') for record in InlineRenderer.numbering_plan.get('displays', {}).get(member, []))):
                    raise ExportError('Math composition supports unnumbered native mathematics without external style spans/footnotes')
            text_renderer = lambda text: InlineRenderer(text, [], math_glyph_fallbacks,
                unsupported_math_glyphs, {'block_id': owner}, math_tag_fallbacks=math_tag_fallbacks).render()
            def math_renderer(value):
                renderer = InlineRenderer(value, [], math_glyph_fallbacks, unsupported_math_glyphs,
                    {'block_id': owner, 'math_presentation_projection': True},
                    math_tag_fallbacks=math_tag_fallbacks)
                return renderer.math(0, len(value), set())
            code_groups[owner] = render_math_presentation(decision, by_id, text_renderer, math_renderer)
            math_groups[owner] = decision
            code_consumed.update(members[1:])
            render_units[owner] = [m for member in members for m in render_units.get(member, [member])]
    # Compose only explicit reviewed groups, preserving all source blocks once.
    consumed = set(layout_plan['consumed_ids']) | set(table_plan['consumed_ids'])
    for _, items in chapters:
        composed = []
        for ident, content in items:
            group = layout_plan['by_anchor'].get(ident)
            table_group = table_plan['by_anchor'].get(ident)
            if ident in code_groups:
                composed.append((ident, code_groups[ident]))
                rendered_parts[ident] = code_groups[ident]
            elif ident in code_consumed:
                continue
            elif table_group:
                composed.append((ident, compose_text_table(table_group, rendered_parts, by_id)))
                render_units[ident] = table_group['member_ids']
            elif group:
                for fig in group['figures']:
                    actual_width = selected[fig['figure_id']]['actual_width_mm']
                    if abs(actual_width - fig['width_mm']) > 0.01:
                        raise ExportError('Source layout and selected figure disagree on frozen width')
                composed.append((ident, compose_source_layout(group, rendered_parts, by_id)))
                render_units[ident] = group['member_ids']
            elif ident not in consumed:
                composed.append((ident, content))
        items[:] = composed
    display_introductions = []
    reading_ids = {ident for _, items in chapters for ident, _ in items}
    if any(b.get('flow_presentation', {}).get('keep_with_next') is True
           and b.get('block_id', b.get('id')) not in reading_ids for b in blocks):
        raise ExportError('Keep relationship belongs to a consumed layout child; review it at the reading-group boundary')
    for _, items in chapters:
        if items and by_id[items[-1][0]].get('flow_presentation', {}).get('keep_with_next') is True:
            raise ExportError('keep_with_next requires a following reading unit in the same chapter')
        pairs = []
        for index in range(len(items)-1):
            ident, _ = items[index]
            original, following = by_id[ident], by_id[items[index+1][0]]
            flow = original.get('flow_presentation', {})
            explicit = flow.get('keep_with_next')
            short_list = (original['type'] == 'list' and len(original.get('items', [])) == 1)
            inferred = ((original['type'] in {'paragraph','proof','example'} or short_list)
                        and not original.get('footnotes') and 0 < len(original['text'].strip()) <= 100
                        and following['type'] in {'formula','grammar','code'})
            # Never keep across a missing source page or across chapters.
            pages = sorted(block_pages(original) | block_pages(following))
            contiguous = all(y == x+1 for x,y in zip(pages,pages[1:]))
            if explicit is True and not contiguous:
                raise ExportError(f'{ident}: keep_with_next crosses a source-page gap')
            if (explicit is True or explicit is None and inferred) and contiguous:
                pairs.append(index)
        # Merge adjacent links so nested samepage environments do not conflict.
        groups = []
        for index in pairs:
            if groups and groups[-1][1] == index:
                groups[-1][1] = index+1
            else:
                groups.append([index,index+1])
        for first,last in groups:
            for index in range(first,last):
                ident, content = items[index]
                items[index] = (ident, content.rstrip() + r'\nopagebreak[4]')
            ident, content = items[first]
            items[first] = (ident, r"\Needspace{4\baselineskip}\begin{samepage}" + '\n' + content)
            ident, content = items[last]
            items[last] = (ident, content + '\n' + r"\end{samepage}")
            members = [items[i][0] for i in range(first,last+1)]
            for i in range(first,last+1):
                rendered_parts[items[i][0]] = items[i][1]
            display_introductions.append({'block_ids':members, 'source_order_unchanged':True,
                                          'mechanism':'native samepage and needspace'})
    ending_decisions, ending_line_offsets = (keep_chapter_end_marks(chapters, by_id, registry, args.nominal_page_mm)
                                            if not args.no_keep_chapter_end_mark else ([], {}))
    assets = (args.template_dir or Path(__file__).resolve().parent.parent / "assets" / "latex").resolve()
    template = (assets / project['entry']).read_text(encoding="utf-8")
    if template.count(project['placeholder']) != 1:
        raise ExportError('Template entry must contain exactly one body placeholder: ' + project['placeholder'])
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=output.name + "-staging-", dir=output.parent))
    block_map = []
    try:
        (stage / "chapters").mkdir()
        if selected:
            (stage / "images").mkdir()
        copy_template_resources(project, stage)
        if special_assets:
            shutil.copytree(Path(special_workspace.name) / 'special-text', stage / 'special-text')
        special_workspace.cleanup()
        config = {
            "BookPaperWidth": f"{args.paper_width_mm:g}", "BookPaperHeight": f"{args.paper_height_mm:g}",
            "BookTextWidth": f"{args.text_width_mm:g}", "BookFigureScale": f"{args.figure_scale:g}", "BookNominalPageWidth": f"{args.nominal_page_mm:g}",
            "BookCJKFont": escape_text(args.cjk_font), "BookLatinFont": escape_text(args.latin_font),
            "BookCJKEmphasisFont": escape_text(args.cjk_emphasis_font),
            "BookLatinSansFont": escape_text(args.latin_sans_font),
            "BookCJKKaiFont": escape_text(args.cjk_kai_font),
            "BookCodeFontFamily": 'tt',
            "BookCodeFontSize": f"{profile['supported_template_rules']['code'].get('font_size_pt', 10):g}pt",
            "BookCodeBaseline": f"{profile['supported_template_rules']['code'].get('line_spacing_pt', 14):g}pt",
            "BookTitle": escape_text(args.title),
            "BookAuthor": escape_text(args.author),
        }
        code_font = profile['supported_template_rules']['code'].get('font_family')
        if code_font:
            config['BookCodeFontFamily'] = 'bookcode'
        extra_config = (r'\newfontfamily\bookcodefamily{' + escape_text(code_font) + '}' + '\n'
                        + r'\setmonofont{' + escape_text(code_font) + '}' + '\n'
                        if code_font else '')
        (stage / "book-config.tex").write_text("\n".join(f"\\providecommand{{\\{k}}}{{{v}}}" for k, v in config.items()) + "\n", encoding="utf-8")
        # Font declarations must follow fontspec loaded by the class/template.
        support = ((LIST_PREAMBLE + r'\usepackage[normalem]{ulem}' + '\n')
                   if project['legacy'] else r'\input{book-config.tex}' + '\n' + NATIVE_SUPPORT)
        if uses_style(all_blocks, 'kai'):
            if args.engine != 'xelatex':
                raise ExportError('Confirmed Kai spans currently require the XeLaTeX/ctex adapter')
            support += (r'\setCJKfamilyfont{zhkai}{\BookCJKKaiFont}' + '\n'
                        + r'\providecommand{\kaishu}{\CJKfamily{zhkai}}' + '\n')
        support += (r'\providecommand{\ExtractTextGlyph}[1]{{\fontspec{\BookCJKFont}#1}}' + '\n')
        if args.language == 'en':
            if project['legacy']:
                support += r'\ctexset{chapter={name={Chapter\space,},number=\arabic{chapter},aftername=\quad}}' + '\n'
            support += (r'\renewcommand{\figurename}{Figure}\renewcommand{\tablename}{Table}' + '\n'
                        + r'\renewcommand{\contentsname}{Contents}' + '\n')
            prefix = r'\thechapter.' if project['family'] == 'book' else ''
            support += (r'\renewcommand{\thefigure}{' + prefix + r'\arabic{figure}}' + '\n'
                        + r'\renewcommand{\thetable}{' + prefix + r'\arabic{table}}' + '\n')
            if args.engine == 'xelatex':
                support += r'\ifdefined\xeCJKDeclareCharClass\xeCJKDeclareCharClass{HalfLeft}{"2018,"201C}\xeCJKDeclareCharClass{HalfRight}{"2019,"201D}\fi' + '\n'
        if special_assets:
            support += extra_config + SPECIAL_TEXT_PREAMBLE
            if code_font:
                support += (r'\makeatletter\def\FV@fontfamily@bookcode{\def\FV@FontFamily{\bookcodefamily}}\makeatother' + '\n')
                # Set after registering the Fancyvrb family; its key executes
                # immediately and otherwise records an unknown NFSS family.
                support += r'\fvset{fontfamily=bookcode}' + '\n'
        if template.count(r'\begin{document}') != 1:
            raise ExportError('Template must have exactly one begin{document}')
        (stage / 'export-support.tex').write_text(support, encoding='utf8')
        template = template.replace(r'\begin{document}', r'\input{export-support.tex}' + '\n' + r'\begin{document}')
        chapter_inputs = []
        frontmatter_started, mainmatter_started = False, False
        previous_source_page, source_gaps = None, []
        for index, (chapter_id, items) in enumerate(chapters, 1):
            part = part_plan.get(chapter_id, {})
            column_start = None
            if part.get('columns') == 2:
                title_position = next((i for i, (ident, _) in enumerate(items)
                                       if ident == part['title_block_id']), None)
                if title_position != 0 or len(items) < 2:
                    raise ExportError('Two-column part needs title first and complete body')
                column_start = title_position + 1
            name = f"chapters/{index:03d}-{safe_name(chapter_id)}.tex"
            text, line = [], 1
            for unit_index, (ident, content) in enumerate(items):
                if unit_index == column_start:
                    text.append(r'\begin{multicols}{2}' + '\n')
                    line += 1
                content = content.strip('\n')
                context_commands = [command for member in render_units.get(ident, [ident])
                                    for command in numbering_plan.get('chapter_contexts', {}).get(member, [])]
                if context_commands:
                    content = '\n'.join(context_commands) + '\n' + content
                unit_pages = sorted({p for member in render_units.get(ident, [ident])
                                     for p in block_pages(by_id[member])})
                if unit_pages:
                    first_page, last_page = unit_pages[0], unit_pages[-1]
                    if previous_source_page is not None and first_page > previous_source_page + 1:
                        omitted = [p for p in range(previous_source_page + 1, first_page) if p not in actual_pages]
                        if omitted:
                            marker = (r'\clearpage\markboth{}{}\noindent{\footnotesize\sffamily '
                                      + f'Source excerpt: physical page {first_page}; preceding source pages omitted.'
                                      + r'\par}\medskip' + '\n')
                            if by_id[ident]['type'] == 'heading' and by_id[ident].get('heading_depth') == 1 and project['family'] == 'book':
                                # chapter already clears the page; a preceding
                                # note would occupy a spurious otherwise blank page.
                                content += '\n' + marker.removeprefix(r'\clearpage').replace(r'\markboth{}{}','',1)
                            else:
                                content = marker + content
                            source_gaps.append({'before_block_id':ident,'previous_physical_page':previous_source_page,
                                                'next_physical_page':first_page,'omitted_physical_pages':omitted,
                                                'editorial_marker_not_source_content':True})
                    previous_source_page = max(previous_source_page or 0, last_page)
                separator = (boundary_separator(by_id[items[unit_index + 1][0]], items[unit_index + 1][1], starts_display)
                             if unit_index + 1 < len(items) else '\n\n')
                text.append(content + separator)
                block = by_id[ident]
                unit_ids = render_units.get(ident, [ident])
                for member_id in unit_ids:
                    member = by_id[member_id]
                    snippet = rendered_parts.get(member_id, '').strip('\n')
                    offset = content.find(snippet) if snippet else -1
                    mapping = {"block_id": member_id, "chapter_id": chapter_id, "tex_file": name,
                               "tex_line": line + (content[:offset].count('\n') if offset >= 0 else 0)
                                           + ending_line_offsets.get(ident, 0),
                               "pages": sorted(block_pages(member)), "sources": member.get('sources', []),
                               "bbox": member.get('bbox'), "text_sha256": sha256(member['text'].encode()).hexdigest()}
                    if member.get('page') in covers:
                        mapping.update(rendered_as='whole_page_cover', cover_page=member['page'],
                                       cover_mode=covers[member['page']]['mode'], cover_transcription=False)
                    elif member_id in contents_plan['source_block_ids']:
                        mapping.update(rendered_as='native_table_of_contents',
                                       source_directory_transcription=False,
                                       generated_from_body_headings=True)
                    elif ident in table_plan['by_anchor']:
                        mapping.update(table_layout_group_id=table_plan['by_anchor'][ident]['group_id'])
                    elif ident in layout_plan['by_anchor']:
                        mapping.update(layout_group_id=layout_plan['by_anchor'][ident]['group_id'],
                                       layout_has_sequential_fallback=False)
                    elif ident in math_groups:
                        mapping.update(rendered_in_math_group_of=ident,
                                       tex_line_scope='containing_group')
                    elif ident in code_groups:
                        mapping.update(rendered_in_code_group_of=ident,
                                       tex_line_scope='member' if offset >= 0 else 'containing_group')
                    block_map.append(mapping)
                for note in rendered_footnotes.get(ident, []):
                    note_latex = (note['heading_text_latex'] if by_id[ident]['type'] == 'heading'
                                  else note['latex'])
                    location = content.find(note_latex)
                    if location < 0 or content.find(note_latex, location + 1) >= 0:
                        raise ExportError(f"{ident}: footnote body was not rendered exactly once")
                    note_line = line + content[:location].count("\n")
                    note.update(tex_file=name, tex_line=note_line)
                    if by_id[ident]['type'] == 'heading':
                        note.update(rendered_latex=note_latex, rendering='native_heading_mark_and_text')
                    for mapping in note["children"]:
                        child_id = mapping["block_id"]
                        child = footnote_children[child_id]
                        mapping.update(tex_file=name, tex_line=note_line + mapping["line_offset"])
                        block_map.append({"block_id": child_id, "chapter_id": chapter_id, "tex_file": name,
                                          "tex_line": mapping["tex_line"], "rendered_in_footnote_of": ident,
                                          "footnote_marker": note["marker"], "page": child.get("page"),
                                          "pages": sorted(block_pages(child)), "bbox": child.get("bbox"),
                                          "sources": child.get("sources", []), "source_sha256": child["source_sha256"],
                                          "text_sha256": sha256(child["text"].encode()).hexdigest()})
                if ident in attached_captions and ident not in render_units:
                    cap_id = attached_captions[ident]
                    caption = by_id[cap_id]
                    block_map.append({"block_id": cap_id, "chapter_id": chapter_id, "tex_file": name,
                                      "tex_line": line, "rendered_with_" + block["type"] + "_block": ident,
                                      "pages": sorted(block_pages(caption)), "sources": caption.get("sources", []),
                                      "bbox": caption.get("bbox"),
                                      "text_sha256": sha256(caption["text"].encode()).hexdigest()})
                line += content.count("\n") + separator.count("\n")
            if column_start is not None:
                text.append(r'\end{multicols}' + '\n')
            (stage / name).write_text("".join(text), encoding="utf-8")
            phase_commands = []
            if index == 1 and chapter_id.startswith('front-'):
                phase_commands = ([r'\frontmatter'] if project['family'] == 'book' else []) + [r'\pagenumbering{Roman}']
                frontmatter_started = True
            elif (frontmatter_started and not mainmatter_started
                  and re.fullmatch(r'chapter-\d+', chapter_id)):
                phase_commands = [r'\mainmatter'] if project['family'] == 'book' else [r'\pagenumbering{arabic}']
                mainmatter_started = True
            chapter_inputs.append('\n'.join(phase_commands + [f"\\input{{{name}}}"]))
        main = annotate_main(template.replace(project['placeholder'], "\n\\clearpage\n".join(chapter_inputs)), args.engine)
        body_sources = '\n'.join(p.read_text(encoding='utf8') for p in (stage/'chapters').glob('*.tex'))
        body_extensions = []
        if r'\ExtractTextGlyph{' in body_sources:
            body_extensions.append('text-glyph-fallback-v1')
            if args.engine == 'pdflatex':
                raise ExportError('Text glyph fallback currently requires a fontspec-capable template engine')
        if any(b['type'] == 'code' and (b.get('annotations') or b.get('code_presentation',{}).get('annotations')
               or any(s['kind'] == 'underline' for s in b.get('styles',[]))) for b in all_blocks):
            body_extensions.append('annotated-code-v1')
        if not project['legacy'] and not set(body_extensions) <= set(project.get('body_extensions', [])):
            raise ExportError('Template manifest must select the used shared body extensions: ' + ', '.join(body_extensions))
        (stage / "main.tex").write_text(main, encoding="utf-8")
        delivery_fonts = font_dependencies(
            {name: getattr(args, name) for name in ('cjk_font', 'cjk_emphasis_font', 'latin_font',
                                                   'latin_sans_font', 'cjk_kai_font')},
            kai_used=uses_style(all_blocks, 'kai'), code_family=code_font, code_used=bool(special_assets))
        delivery_fonts.update(engine=args.engine, template_project=not project['legacy'])
        (stage / 'BUILD.txt').write_text(build_instructions(delivery_fonts), encoding='utf-8')
        (stage / '.latexmkrc').write_text(latexmkrc(args.engine), encoding='utf-8')
        (stage / 'export-template.json').write_text(json.dumps(
            {k:v for k,v in project.items() if k != 'root'} | {'resource_hashes':template_hashes(project)},
            indent=2, ensure_ascii=False), encoding='utf8')
        for entry in selected.values():
            shutil.copyfile(entry["path"], stage / entry["package_path"])
        for entry in selected_covers.values():
            if entry['mode'] == 'image':
                (stage / 'covers').mkdir(exist_ok=True)
                shutil.copyfile(entry['asset_path'], stage / entry['package_path'])
        if output.exists():
            if not output.is_dir():
                raise ExportError("existing output is not a directory")
            shutil.rmtree(output)
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    report = {
        "schema_version": "latex-export-v1", "source_sha256": source_hash,
        "working_pdf_sha256s": sorted({b["working_pdf_sha256"] for b in blocks if b.get("working_pdf_sha256")}),
        "source_pdf_hash_verified": bool(args.source_pdf), "declared_pages": expected_pages,
        "semantic_pages": actual_pages, "semantic_block_count": len(blocks),
        "footnote_child_block_count": len(footnote_children),
        "page_coverage_is_semantic_not_visual_completeness": True,
        "inputs": inputs, "output_dir": str(output), "build_dir": str(build),
        "template": {"path": str(assets), "entry":project['entry'], "engine":args.engine,
                     "family":project['family'], "resource_hashes":template_hashes(project)},
        "font_dependencies": delivery_fonts,
        "body_interface":{'extensions':body_extensions,
                          'internal_commands':sorted(set(re.findall(r'\\(Book[A-Za-z]+)', body_sources))),
                          'ordinary_content_uses_native_and_package_commands':True},
        "dimensions": {"nominal_page_mm": args.nominal_page_mm, "text_width_mm": args.text_width_mm,
                       "scale": args.text_width_mm / args.nominal_page_mm, "figure_scale": args.figure_scale,
                       "effective_figure_scale": args.figure_scale * args.text_width_mm / args.nominal_page_mm},
        "evidence_counts": dict(unreviewed), "warnings": warnings, "blocks": block_map,
        "format_admission": format_admission, "figure_coverage": figure_coverage, "special_text_assets": special_assets,
        "code_groups": {k: render_units[k] for k in code_groups if k not in math_groups},
        "math_groups": math_groups,
        "structural_presentations": [{"block_id": b.get('block_id', b.get('id')),
            **{k: b[k] for k in ('paragraph_presentation', 'heading_presentation', 'list_presentation',
                               'code_presentation', 'grammar_presentation', 'flow_presentation', 'group_presentation', 'merged_presentations') if k in b}}
            for b in blocks if any(k.endswith('_presentation') for k in b) or b.get('merged_presentations')],
        "deferred_math_style_candidates": deferred_math_styles,
        "deferred_math_style_counts": {
            "total": len(deferred_math_styles),
            "by_kind": dict(Counter(s["style"]["kind"] for s in deferred_math_styles)),
            "by_evidence": dict(Counter(s["style"].get("evidence", "unreviewed") for s in deferred_math_styles)),
            "by_reason": dict(Counter(s["reason"] for s in deferred_math_styles)),
            "existing_latex_matches_candidate": sum(s["existing_latex_matches_candidate"] for s in deferred_math_styles),
        },
        "redundant_confirmed_or_rule_math_styles": redundant_math_styles,
        "math_glyph_fallbacks": math_glyph_fallbacks,
        "math_glyph_fallback_counts": dict(Counter(item["codepoint"] for item in math_glyph_fallbacks)),
        "unsupported_math_unicode_characters": unsupported_math_glyphs,
        "math_tag_fallbacks": math_tag_fallbacks,
        "inline_math_line_breaks": InlineRenderer.math_line_breaks,
        "math_text_command_fallbacks": InlineRenderer.math_command_fallbacks,
        "native_numbering": numbering_plan,
        "table_of_contents": contents_plan,
        "page_numbering": {"page_style": "plain", "number_position": "footer",
                           "frontmatter_roman": frontmatter_started,
                           "mainmatter_arabic_from_one": mainmatter_started,
                           "native_commands": [r'\frontmatter', r'\pagenumbering{Roman}', r'\mainmatter']
                           if frontmatter_started else [],
                           "cover_pages_empty_style": True},
        "document_parts": part_plan,
        "source_gaps":source_gaps,
        "list_presentations": list_presentations,
        "review_annotations": InlineRenderer.review_annotations,
        "numbered_systems": [{"block_id": b.get('block_id', b.get('id')),
                              "contract": b['numbered_system']}
                             for b in all_blocks if b.get('numbered_system')],
        "proof_endings": proof_decisions,
        "display_introductions": display_introductions,
        "chapter_end_keep_with_previous": ending_decisions,
        "source_figure_layouts": layout_plan,
        "source_table_presentations": table_plan,
        "inline_labels": [{"block_id": b.get('block_id', b.get('id')), **b['inline_label']}
                          for b in all_blocks if b.get('inline_label')],
        "covers": [{**entry, 'rendered_source_block_ids': cover_members[page]}
                   for page, entry in selected_covers.items()],
        "footnotes": [{key: value for key, value in note.items() if key != "latex"}
                      for notes in rendered_footnotes.values() for note in notes],
        "ignored_blocks": ignored,
        "figures": {key: {k: v for k, v in entry.items() if k != "path"} for key, entry in selected.items()},
        "compile": {"requested": args.compile, "success": False},
    }
    sidecar.parent.mkdir(parents=True, exist_ok=True)

    def save_report():
        report["package_files"] = [{"path": str(p.relative_to(output)), "sha256": digest(p)}
                                   for p in sorted(output.rglob("*")) if p.is_file()]
        sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    save_report()
    report["timings"] = {"source_export_seconds": time.perf_counter() - export_started}
    save_report()
    if args.compile:
        compile_started = time.perf_counter()
        executable = shutil.which(args.engine)
        if not executable:
            raise ExportError(f"XeLaTeX engine is unavailable: {args.engine}; source package and sidecar were retained")
        build.mkdir(parents=True, exist_ok=True)
        command = [executable, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                   "-file-line-error", "-recorder", f"-output-directory={build}", "main.tex"]
        report["compile"]["command"] = command
        previous_state, stable = None, False
        # A newly generated directory changes pagination after its first read.
        # Compare auxiliary state instead of assuming two passes are sufficient.
        max_runs = max(args.compile_runs, 5) if contents_plan['enabled'] else args.compile_runs
        pass_states = []
        for run in range(1, max_runs + 1):
            process = subprocess.run(command, cwd=output, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, timeout=args.compile_timeout)
            (build / f"{Path(args.engine).name}-run-{run}.log").write_text(process.stdout, encoding="utf-8")
            report["compile"]["returncode"] = process.returncode
            if process.returncode:
                save_report()
                raise ExportError(f"{args.engine} failed on run {run}; inspect {build / f'{Path(args.engine).name}-run-{run}.log'}")
            current_state = {ext: digest(build / ('main.' + ext))
                             for ext in ('aux', 'toc', 'out') if (build / ('main.' + ext)).is_file()}
            stable = current_state == previous_state
            pass_states.append({'run': run, 'auxiliary_hashes': current_state, 'stable': stable})
            previous_state = current_state
            if run >= args.compile_runs and (not contents_plan['enabled'] or stable):
                break
        report['compile'].update(runs=run, auxiliary_pass_states=pass_states,
                                 auxiliary_state_stable=stable if contents_plan['enabled'] else None)
        if contents_plan['enabled'] and not stable:
            save_report()
            raise ExportError('Automatic directory/references did not stabilize within five passes; inspect retained build')
        pdf = build / "main.pdf"
        if not pdf.is_file():
            raise ExportError("XeLaTeX returned success without producing main.pdf")
        shutil.copyfile(pdf, output / "main.pdf")
        log = (build / "main.log").read_text(encoding="utf-8", errors="replace")
        report["compile"].update(success=True, runs=run,
                                 dependencies=recorder_dependencies(build / 'main.fls', output),
                                 missing_glyph_count=log.count("Missing character:"),
                                 overfull_box_count=log.count("Overfull "),
                                 underfull_box_count=log.count("Underfull "),
                                 visual_review="not_performed_by_exporter")
        if report["compile"]["missing_glyph_count"] or report["compile"]["overfull_box_count"]:
            report["warnings"].append("Compilation has missing glyphs or overfull boxes; inspect build logs and rendered pages before delivery.")
        report["timings"]["compile_seconds"] = time.perf_counter() - compile_started
        save_report()
    return {"output": str(output), "sidecar": str(sidecar), "blocks": len(blocks),
            "figures": len(selected), "compile": report["compile"], "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", type=Path, required=True, help="semantic blocks JSONL in reading order")
    parser.add_argument("--figures", type=Path, help="final selected figure registry")
    parser.add_argument("--figure-inventory", type=Path, help="independent source-first visual inventory for the exact source scope; required in production")
    parser.add_argument("--figure-root", type=Path, help="root for registry asset paths; default registry parent")
    parser.add_argument("--output", type=Path, required=True, help="clean final source package directory")
    parser.add_argument("--expected-pages", required=True, help="required exact semantic physical-page coverage, e.g. 1-50,53")
    parser.add_argument("--manifest", type=Path, help="optional prepared-page manifest for coverage/input hash audit")
    parser.add_argument("--source", "--source-pdf", dest="source_pdf", type=Path, help="original PDF or numbered PNG directory; identity must match blocks")
    parser.add_argument("--sidecar", type=Path, help="provenance JSON outside output; default OUTPUT-export.json sibling")
    parser.add_argument("--template-dir", type=Path, help="template.json project directory or legacy two-file template")
    parser.add_argument('--format-profile', type=Path, required=True, help='source-bound book format profile')
    parser.add_argument('--template-ready', type=Path, help='accepted hash-verified template admission for production')
    parser.add_argument('--preflight-mode', action='store_true', help='nonproduction template sample, at most eight source pages')
    parser.add_argument("--nominal-page-mm", type=float, default=None, help="book-wide nominal source page width")
    parser.add_argument("--figure-scale", type=float, default=None, help="shared book-wide readability scale; applied once to all body figures")
    parser.add_argument("--table-presentations", type=Path, help="source-reviewed per-table widths and text/table arrangements")
    parser.add_argument("--math-presentations", type=Path, help="source-reviewed contiguous unnumbered math groups/left labels")
    parser.add_argument("--proof-endings", type=Path, help="explicit source-reviewed QED bindings")
    parser.add_argument("--numbering-overrides", type=Path, help="explicit source-reviewed number typography corrections")
    parser.add_argument("--text-width-mm", type=float, default=None, help="template actual text width")
    parser.add_argument("--paper-width-mm", type=float, default=None)
    parser.add_argument("--paper-height-mm", type=float, default=None)
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--cjk-font", default=None, help="installed Chinese font; saved in book-config.tex")
    parser.add_argument("--latin-font", default=None, help="installed Latin font with text prime glyphs")
    parser.add_argument("--cjk-emphasis-font", default=None, help="regular-weight Heiti family for ordinary-text emphasis")
    parser.add_argument("--latin-sans-font", default=None)
    parser.add_argument('--cjk-kai-font', default=None, help='verified installed Kai family for explicit kai spans')
    parser.add_argument("--figure-layouts", type=Path, help="explicit source-reviewed layout decisions")
    parser.add_argument("--covers", type=Path, help="reviewed full-page cover manifest")
    parser.add_argument("--compile", action="store_true", help="compile with the template engine; inspect PDF before delivery")
    parser.add_argument("--engine", default=None, help="LaTeX binary, consistent with the template project and profile")
    parser.add_argument("--build-dir", type=Path, help="aux/log/PDF directory outside output; default OUTPUT-build sibling")
    parser.add_argument("--compile-runs", type=int, choices=(1, 2, 3), default=2,
                        help="minimum passes; automatic contents may run up to five passes until auxiliary state is stable")
    parser.add_argument("--compile-timeout", type=int, default=180, help="timeout seconds per XeLaTeX run")
    parser.add_argument("--force", action="store_true", help="replace output/sidecar explicitly and reuse build directory")
    parser.add_argument("--no-keep-chapter-end-mark", action="store_true", help="disable conservative small chapter-end mark grouping")
    args = parser.parse_args()
    try:
        print(json.dumps(export(args), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"export_latex: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
