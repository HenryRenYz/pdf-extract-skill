"""Renderer-independent structured, HTML and Markdown table parsing."""
from __future__ import annotations
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import re

class TableDataError(ValueError):
    pass

ExportError = TableDataError

def escaped_at(text, offset):
    n = 0
    while offset > 0 and text[offset-1] == "\\":
        offset -= 1
        n += 1
    return bool(n % 2)

@dataclass
class Cell:
    text: str
    rowspan: int = 1
    colspan: int = 1
    align: str = "c"
    valign: str = "c"
    styles: list[dict] | None = None
    header: bool = False


def positive_span(value: object) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ExportError(f"invalid table span: {value}") from None
    if str(result) != str(value) or not 1 <= result <= 256:
        raise ExportError(f"invalid table span: {value}")
    return result


def structured_rows(cells):
    """Accept row arrays or explicit zero-based row/col anchors, without guessing."""
    if not isinstance(cells, list):
        raise ExportError('table cells must be row arrays or explicit coordinate cells')
    if all(isinstance(row, list) for row in cells):
        return cells
    if not cells or not all(isinstance(cell, dict) for cell in cells):
        raise ExportError('table cannot mix row arrays and coordinate cells')
    rows, occupied = {}, set()
    ordered = []
    for cell in cells:
        r, c = cell.get('row'), cell.get('col', cell.get('column'))
        if (type(r) is not int or type(c) is not int or min(r, c) < 0
                or 'col' in cell and 'column' in cell and cell['col'] != cell['column']):
            raise ExportError('coordinate cells need unambiguous zero-based row/col integers')
        ordered.append((r, c, cell))
    for r, c, cell in sorted(ordered, key=lambda item: item[:2]):
        # A sparse coordinate hole cannot silently shift a later cell left.
        cursor = 0
        while cursor < c:
            if (r, cursor) not in occupied:
                raise ExportError('coordinate table has a hole before a cell')
            cursor += 1
        rowspan, colspan = positive_span(cell.get('rowspan', 1)), positive_span(cell.get('colspan', 1))
        area = {(y, x) for y in range(r, r+rowspan) for x in range(c, c+colspan)}
        if occupied & area:
            raise ExportError('coordinate table cells overlap')
        occupied |= area
        rows.setdefault(r, []).append(cell)
    height = max(r for r, c in occupied)+1
    width = max(c for r, c in occupied)+1
    if len(occupied) != height*width:
        raise ExportError('coordinate table has uncovered grid positions')
    return [rows.get(r, []) for r in range(height)]


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.rows: list[list[Cell]] = []
        self.current: Cell | None = None
        self.depth = 0
        self.table_count = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "table":
            self.table_count += 1
            if self.table_count > 1:
                raise ExportError("multiple HTML tables need source-reviewed table_parts; cannot flatten independent grids")
            self.depth += 1
            if self.depth != 1:
                raise ExportError("nested HTML tables need an explicit semantic conversion")
        elif tag == "tr":
            if self.current is not None:
                raise ExportError("table row starts inside a cell")
            self.rows.append([])
        elif tag in ("td", "th"):
            if not self.rows or self.current is not None:
                raise ExportError("malformed table cell structure")
            align = attributes.get("align", "center")
            style = attributes.get("style", "").replace(" ", "")
            valign = "t" if "vertical-align:top" in style else ("b" if "vertical-align:bottom" in style else "c")
            self.current = Cell("", positive_span(attributes.get("rowspan", 1)),
                                positive_span(attributes.get("colspan", 1)),
                                {"left": "l", "right": "r", "center": "c"}.get(align, "c"),
                                valign, header=tag == "th")
            self.rows[-1].append(self.current)
        elif self.current is not None:
            self.current.text += self.get_starttag_text()
        elif tag not in ("thead", "tbody", "tfoot", "caption"):
            raise ExportError(f"unsupported table tag: {tag}")

    def handle_startendtag(self, tag, attrs):
        if self.current is not None:
            self.current.text += self.get_starttag_text()

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            if self.current is None:
                raise ExportError("unmatched table cell closing tag")
            self.current = None
        elif tag == "table":
            self.depth -= 1
        elif self.current is not None:
            self.current.text += f"</{tag}>"

    def handle_data(self, data):
        if self.current is not None:
            self.current.text += data
        elif data.strip():
            raise ExportError("text outside table cells; make the caption a semantic caption block")

    def handle_entityref(self, name):
        self.handle_data(unescape(f"&{name};"))

    def handle_charref(self, name):
        self.handle_data(unescape(f"&#{name};"))


def braced(text, start):
    if start >= len(text) or text[start] != '{':
        raise ExportError('Expected a literal braced table argument')
    depth = 0
    for end in range(start, len(text)):
        if not escaped_at(text, end):
            depth += (text[end] == '{') - (text[end] == '}')
            if depth == 0:
                return text[start+1:end], end+1
    raise ExportError('Unclosed braced table argument')


def latex_table_rows(text):
    """Read a literal simple tabular, never expand TeX or infer its visual rules.

    Macro-generated rows, nested environments and multirow/multicolumn need
    explicit structured cells. The source block text remains byte-identical.
    """
    prefix = re.match(r'\\begin\{tabular\}\s*(?:\[[^\]]*\]\s*)?', text)
    if not prefix:
        raise ExportError('Unsupported LaTeX table; provide explicit structured cells')
    _, begin = braced(text, prefix.end())
    ending = re.search(r'\\end\{tabular\}\s*$', text)
    if ending is None:
        raise ExportError('Unclosed LaTeX tabular')
    body = text[begin:ending.start()]
    if re.search(r'\\(?:multirow|multicolumn|input|include|newcommand|def|begin|end|cr|tabularnewline)\b', body):
        raise ExportError('Complex LaTeX table needs explicit structured cells; no macro expansion')
    rows, row, buf, depth, math_end, i = [], [], [], 0, None, 0
    def cell():
        raw = ''.join(buf).strip()
        styles = []
        for macro, kind in [('textbf', 'bold'), ('textit', 'italic'), ('emph', 'italic')]:
            prefix = '\\'+macro
            if raw.startswith(prefix+'{'):
                content, finish = braced(raw, len(prefix))
                if finish == len(raw):
                    raw = content
                    if raw:
                        styles.append(dict(start=0, end=len(raw), kind=kind, evidence='unreviewed'))
                    break
        row.append(Cell(raw, styles=styles))
        buf.clear()
    while i < len(body):
        if body[i] == '%' and not escaped_at(body, i):
            end = body.find('\n', i)
            i = len(body) if end < 0 else end+1
            continue
        if math_end:
            if body.startswith(math_end, i) and not escaped_at(body, i):
                buf.append(math_end)
                i += len(math_end)
                math_end = None
                continue
        else:
            opener = next((m for m in (r'\(', r'\[', '$$', '$') if body.startswith(m, i) and not escaped_at(body, i)), None)
            if opener:
                math_end = {r'\(': r'\)', r'\[': r'\]'}.get(opener, opener)
                buf.append(opener)
                i += len(opener)
                continue
            if depth == 0:
                ruling = re.match(r'\\(?:hline|toprule|midrule|bottomrule)\b|\\cline\{[0-9]+-[0-9]+\}', body[i:])
                if ruling:
                    if row or ''.join(buf).strip():
                        raise ExportError('LaTeX table ruling inside a data row')
                    buf.clear()
                    i += ruling.end()
                    continue
                if body.startswith('\\\\', i):
                    cell()
                    rows.append(row)
                    row = []
                    i += 2
                    if re.match(r'\s*(?:\[|\*)', body[i:]):
                        raise ExportError('LaTeX row spacing/star modifiers need explicit structured cells')
                    continue
                if body[i] == '&' and not escaped_at(body, i):
                    cell()
                    i += 1
                    continue
            command = re.match(r'\\([A-Za-z@]+)', body[i:])
            if command and command[1] not in {'textbf', 'textit', 'emph', 'textrm', 'textnormal',
                                               'texttt', 'textsf', 'textsc', 'textup', 'textsl', 'underline'}:
                raise ExportError('Unsupported LaTeX table macro; provide explicit structured cells')
        if body[i] in '{}' and not escaped_at(body, i):
            depth += 1 if body[i] == '{' else -1
            if depth < 0:
                raise ExportError('Unbalanced braces in LaTeX table')
        buf.append(body[i])
        i += 1
    if depth or math_end:
        raise ExportError('Unclosed math/braces in LaTeX table')
    if row or ''.join(buf).strip():
        cell()
        rows.append(row)
    return rows


def table_rows(block: dict) -> list[list[Cell]]:
    cells = block.get("cells")
    if cells:
        # Preferred structured format: rows of cell dictionaries, with Unicode styles.
        cells = structured_rows(cells)
        rows = []
        for row in cells:
            result = []
            for item in row:
                if isinstance(item, str):
                    item = {"text": item}
                if not isinstance(item, dict) or not isinstance(item.get("text", ""), str):
                    raise ExportError("structured table cells must be strings or dictionaries with text strings")
                result.append(Cell(str(item.get("text", "")), positive_span(item.get("rowspan", 1)),
                                   positive_span(item.get("colspan", 1)), item.get("align", "c"),
                                   item.get("valign", "c"), item.get("styles", []), item.get("header", False)))
            rows.append(result)
        return rows
    text = block["text"].strip()
    if text.startswith(r'\begin'):
        return latex_table_rows(text)
    if text.lower().startswith("<table"):
        parser = TableParser()
        parser.feed(text)
        parser.close()
        if parser.depth != 0 or parser.current is not None:
            raise ExportError("unclosed HTML table")
        return parser.rows
    # Legacy pipe tables: don't split a mathematical | into a new cell.
    rows = []
    for line in text.splitlines():
        parts, buf, math_end, i = [], [], None, 0
        line = line.strip()
        if not line:
            continue
        while i < len(line):
            if math_end:
                if line.startswith(math_end, i) and not escaped_at(line, i):
                    buf.append(math_end)
                    i += len(math_end)
                    math_end = None
                    continue
            else:
                opener = next((m for m in (r"\(", "$") if line.startswith(m, i) and not escaped_at(line, i)), None)
                if opener:
                    math_end = r"\)" if opener == r"\(" else "$"
                    buf.append(opener)
                    i += len(opener)
                    continue
                if line[i] == "|" and not escaped_at(line, i):
                    parts.append("".join(buf).strip())
                    buf = []
                    i += 1
                    continue
            buf.append(line[i])
            i += 1
        if math_end:
            raise ExportError("unclosed math in pipe table")
        parts.append("".join(buf).strip())
        if line.startswith("|"):
            parts.pop(0)
        if line.endswith("|"):
            parts.pop()
        if all(re.fullmatch(r":?-{3,}:?", p) for p in parts):
            continue
        rows.append([Cell(p) for p in parts])
    return rows
