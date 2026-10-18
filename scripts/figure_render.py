#!/usr/bin/env python3
"""Fixed physical-size Matplotlib exporter for independent figure workers.

The worker owns draw(), not the canvas dimensions or the shared style. PNG is
always 400 dpi. PDF/SVG are emitted only when artist inspection finds no raster
content, unless the coordinator explicitly permits a labelled mixed-vector
export. This is structural validation, not visual acceptance of the figure.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import warnings
from contextlib import contextmanager
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, ft2font, mathtext
from matplotlib.image import AxesImage, FigureImage
from matplotlib.text import Text

DPI = 400


@contextmanager
def no_missing_glyphs():
    """Mathtext reports some substitutions through logging, not warnings."""
    messages = []
    class Capture(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if "glyph" in message.lower() and ("not have" in message.lower() or "missing" in message.lower()):
                messages.append(message)
    handler = Capture()
    logger = logging.getLogger("matplotlib")
    logger.addHandler(handler)
    try:
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter("always")
            yield
        messages.extend(str(n.message) for n in notices if "glyph" in str(n.message).lower())
    finally:
        logger.removeHandler(handler)
    if messages:
        raise ValueError(f"Font glyph substitution rejected: {messages}")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_font_face(props):
    """Reject both missing families and silent regular-face substitutions."""
    resolved = font_manager.findfont(props, fallback_to_default=False)
    face = ft2font.FT2Font(resolved)
    flags = getattr(face.style_flags, "value", face.style_flags)
    if props.get_style() in {"italic", "oblique"} and not flags & 1:
        raise ValueError(f"Requested italic face unavailable: {props.get_family()} resolved to {face.style_name}")
    weight = props.get_weight()
    weight = font_manager.weight_dict.get(weight, 400) if isinstance(weight, str) else weight
    if weight >= 700 and not flags & 2:
        raise ValueError(f"Requested bold face unavailable: {props.get_family()} resolved to {face.style_name}")
    return resolved


def load_spec(path):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    for name in ("id", "source", "canvas", "style_version"):
        if name not in spec:
            raise ValueError(f"Missing spec.{name}")
    src = spec["source"]
    for name in ("pdf_sha256", "page", "bbox_px", "page_size_px", "page_size_pt"):
        if name not in src:
            raise ValueError(f"Missing spec.source.{name}")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", src["pdf_sha256"]):
        raise ValueError("source.pdf_sha256 must be a SHA-256 digest")
    if not isinstance(src["page"], int) or isinstance(src["page"], bool) or src["page"] < 1:
        raise ValueError("source.page is a one-based physical PDF page number")
    for key in ("page_size_px", "page_size_pt"):
        if len(src[key]) != 2 or not all(math.isfinite(v) and v > 0 for v in src[key]):
            raise ValueError(f"Invalid source.{key}")
    box = src["bbox_px"]
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        raise ValueError("bbox_px must contain four finite numbers")
    x0, y0, x1, y1 = box
    pw, ph = src["page_size_px"]
    if not (0 <= x0 < x1 <= pw and 0 <= y0 < y1 <= ph):
        raise ValueError("bbox_px must be a non-empty box inside the source page")
    for key in ("width_mm", "height_mm"):
        if not math.isfinite(spec["canvas"].get(key, 0)) or spec["canvas"].get(key, 0) <= 0:
            raise ValueError(f"Invalid canvas.{key}")
    return spec


def resolve_style(path, spec):
    path = Path(path)
    if spec.get("style_sha256") and spec["style_sha256"] != sha256(path):
        raise ValueError("Frozen style hash differs from the supplied profile")
    style = json.loads(path.read_text(encoding="utf-8"))
    if spec["style_version"] != style["version"]:
        raise ValueError("Frozen spec.style_version does not match the supplied profile")
    overrides = spec.get("style_override", {})
    if overrides and not spec.get("style_override_reviewed", False):
        raise ValueError("Local style overrides require style_override_reviewed: true")
    forbidden = set(overrides) - set(style.get("allowed_overrides", []))
    if forbidden:
        raise ValueError(f"Unapproved style override keys: {sorted(forbidden)}")
    style.update(overrides)
    font = style["font"]
    for value in font.get("paths", []):
        font_path = Path(value).expanduser()
        if not font_path.is_absolute():
            font_path = path.parent / font_path
        if not font_path.is_file():
            raise ValueError(f"Configured font file not found: {font_path}")
        font_manager.fontManager.addfont(str(font_path))
    font_paths = set()
    for face in font.get("required_styles", ["normal"]):
        if face not in {"normal", "bold", "italic", "bold_italic"}:
            raise ValueError(f"Unsupported required font style: {face}")
        props = font_manager.FontProperties(
            family=font["family"], weight="bold" if "bold" in face else "normal",
            style="italic" if "italic" in face else "normal")
        resolved = require_font_face(props)
        font_paths.add(str(Path(resolved).resolve()))
    if style["mathtext_fontset"] not in {"dejavusans", "dejavuserif", "cm", "stix", "stixsans"}:
        raise ValueError("Choose a built-in mathtext fontset; custom fontsets need a separately audited profile")
    return style, font_paths


def rc_style(style):
    return {
        "font.family": style["font"]["family"],
        "font.size": style["font_size_pt"],
        "mathtext.fontset": style["mathtext_fontset"],
        "text.usetex": False,
        "axes.labelsize": style["label_size_pt"],
        "xtick.labelsize": style["tick_size_pt"],
        "ytick.labelsize": style["tick_size_pt"],
        "legend.fontsize": style["legend_size_pt"],
        "lines.linewidth": style["line_width_pt"],
        "lines.markersize": style["marker_size_pt"],
        "axes.linewidth": style["axis_width_pt"],
        "xtick.major.width": style["tick_width_pt"],
        "ytick.major.width": style["tick_width_pt"],
        "xtick.major.size": style["tick_length_pt"],
        "ytick.major.size": style["tick_length_pt"],
        "text.color": style["color"],
        "axes.labelcolor": style["color"],
        "axes.edgecolor": style["color"],
        "xtick.color": style["color"],
        "ytick.color": style["color"],
        "axes.prop_cycle": matplotlib.cycler(color=[style["color"]]),
        "figure.facecolor": style["background"],
        "axes.facecolor": style["background"],
        "savefig.facecolor": style["background"],
        "grid.color": style["grid_color"],
        "grid.linewidth": style["grid_width_pt"],
        "pdf.fonttype": 42,
        "svg.fonttype": "path",
        "savefig.bbox": None,
        "savefig.pad_inches": 0,
    }


def pdf_fonttype(style, paths):
    # CFF/OpenType CJK collections can pass glyph coverage but render blank
    # when embedded as CIDFontType2. Type3 emits vector glyph outlines instead.
    from fontTools.ttLib import TTFont
    cff = []
    for path in paths:
        with TTFont(path, fontNumber=0, lazy=True) as face:
            if "CFF " in face or "CFF2" in face:
                cff.append(path)
    configured = style.get("pdf_fonttype", "auto")
    if configured not in {"auto", 3, 42}:
        raise ValueError("pdf_fonttype must be auto, 3 or 42")
    if configured == 42 and cff:
        raise ValueError("CFF font requires vector Type3 glyph export; do not force Type42")
    selected = 3 if configured == 3 or cff else 42
    return selected, {"selection": configured, "cff_outline_fonts": sorted(cff),
                      "note": "Type3 glyph paths remain vector; text extraction may not retain all Unicode labels, kept separately in scene metadata."}


def label_backgrounds(fig, style):
    policy = style.get("label_background", {"enabled": True, "mode": "selective",
                                            "facecolor": "#ffffff", "alpha": 1.0,
                                            "padding_em": 0.04, "zorder": 10})
    if not isinstance(policy, dict):
        raise ValueError("label_background must be an object")
    alpha, padding = policy.get("alpha", 1.0), policy.get("padding_em", 0.10)
    if not 0 <= alpha <= 1 or not 0 <= padding <= 0.5:
        raise ValueError("Label background alpha must be [0,1] and padding [0,0.5] em")
    mode = policy.get("mode", "selective")
    if mode not in {"selective", "none"}:
        raise ValueError("Label background mode must be selective or none; blanket white backing is not supported")
    backed = []
    if policy.get("enabled", True):
        for text in fig.findobj(Text):
            if not text.get_visible() or not text.get_text().strip():
                continue
            # The drawing author first tries nearby clear positions, then marks
            # only unavoidable overlaps with a stable, non-empty fallback ID.
            # Position trials and justification belong in scene/self-check data.
            gid = text.get_gid() or ""
            if mode == "none" or not gid.startswith("label-background:"):
                continue
            label_id = gid.removeprefix("label-background:")
            if not label_id or any(r["id"] == label_id for r in backed):
                raise ValueError("Fallback label IDs must be non-empty and unique")
            text.set_bbox({"boxstyle": f"square,pad={padding}",
                           "facecolor": policy.get("facecolor", "#ffffff"),
                           "edgecolor": "none", "alpha": alpha})
            text.set_zorder(max(text.get_zorder(), policy.get("zorder", 10)))
            backed.append({"id": label_id, "text": text.get_text()})
    return {**policy, "mode": mode, "labels_backed": len(backed),
            "selected_labels": backed, "vector_patch": True,
            "placement_review": "Author must try repositioning first; no automatic overlap or free-space detection."}


def inspect_artists(fig):
    reasons = []
    for artist in fig.findobj():
        if not artist.get_visible():
            continue
        if isinstance(artist, (AxesImage, FigureImage)):
            reasons.append({"artist": type(artist).__name__, "reason": "embedded pixel image"})
        if getattr(artist, "get_rasterized", lambda: False)():
            reasons.append({"artist": type(artist).__name__, "reason": "rasterized artist"})
        if getattr(artist, "get_agg_filter", lambda: None)() is not None:
            reasons.append({"artist": type(artist).__name__, "reason": "Agg filter requires raster rendering"})
    for ax in fig.axes:
        zorder = ax.get_rasterization_zorder()
        if zorder is not None:
            reasons.append({"artist": type(ax).__name__, "reason": "axes rasterization threshold", "zorder": zorder})
    return reasons


def inspect_fonts(fig):
    """Fail on missing ordinary glyphs; record actual mathtext font files too."""
    paths = set()
    parser = mathtext.MathTextParser("path")
    for artist in fig.findobj(Text):
        if not artist.get_visible() or not artist.get_text():
            continue
        props = artist.get_fontproperties()
        resolved = require_font_face(props)
        paths.add(str(Path(resolved).resolve()))
        face = ft2font.FT2Font(resolved)
        text = artist.get_text()
        # Match unescaped pairs. Mathtext itself verifies the removed segments.
        math_spans = re.findall(r"(?<!\\)\$(.*?)(?<!\\)\$", text)
        ordinary = re.sub(r"(?<!\\)\$.*?(?<!\\)\$", "", text)
        missing = sorted({c for c in ordinary if not c.isspace() and ord(c) not in face.get_charmap()})
        if missing:
            raise ValueError(f"Font {Path(resolved).name} lacks label glyphs {missing!r}; configure a matching font")
        if math_spans:
            # Parse the whole mixed label, including non-math CJK text, because
            # it can use a different font than its mathematical characters.
            with no_missing_glyphs():
                parsed = parser.parse(text, dpi=DPI, prop=props)
            # Matplotlib 3.11 adds a glyph index to this tuple; the font remains
            # first in both the old five-field and new six-field layouts.
            for font, *unused in parsed.glyphs:
                paths.add(str(Path(font.fname).resolve()))
    return paths


def render(draw, spec_path, style_path, output, script_path, formats=("png", "pdf", "svg")):
    """Render a draw(fig, style, spec) callback into an isolated job directory."""
    spec = load_spec(spec_path)
    style, fonts = resolve_style(style_path, spec)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("figure.png", "figure.pdf", "figure.svg", "render-metadata.json")):
        raise ValueError("Use a fresh output directory for each attempt; old exports must not be mistaken for this render")
    formats = tuple(dict.fromkeys(formats))
    if set(formats) - {"png", "pdf", "svg"}:
        raise ValueError("Only png, pdf and svg exports are supported")
    if not formats:
        raise ValueError("At least one export format is required")
    width_mm, height_mm = (spec["canvas"][key] for key in ("width_mm", "height_mm"))
    size = (width_mm / 25.4, height_mm / 25.4)
    files = {}
    with matplotlib.rc_context(rc_style(style)):
        fig = plt.figure(figsize=size, dpi=DPI, layout=None)
        try:
            draw(fig, style, spec)
            if not all(abs(actual - expected) < 1e-9 for actual, expected in zip(fig.get_size_inches(), size)):
                raise ValueError("draw() changed the coordinator's frozen physical canvas")
            # Resolve automatic tick labels, then protect labels from crossing lines.
            with no_missing_glyphs():
                fig.canvas.draw()
            label_policy = label_backgrounds(fig, style)
            with no_missing_glyphs():
                fig.canvas.draw()
            fonts.update(inspect_fonts(fig))
            selected_fonttype, font_export = pdf_fonttype(style, fonts)
            matplotlib.rcParams["pdf.fonttype"] = selected_fonttype
            raster_reasons = inspect_artists(fig)
            vector_kind = "mixed_vector" if raster_reasons else "vector_artists"
            skipped = []
            for extension in formats:
                if extension != "png" and raster_reasons and not spec.get("export", {}).get("allow_mixed_vector", False):
                    skipped.append(extension)
                    continue
                target = output / f"figure.{extension}"
                try:
                    with no_missing_glyphs():
                        fig.savefig(target, format=extension, dpi=DPI, bbox_inches=None, pad_inches=0)
                except ValueError:
                    target.unlink(missing_ok=True)
                    raise
                files[extension] = {"file": target.name, "sha256": sha256(target)}
            png_size = None
            if "png" in files:
                from PIL import Image
                with Image.open(output / files["png"]["file"]) as image:
                    png_size = list(image.size)
                expected = [int(v * DPI) for v in size]
                if png_size != expected:
                    raise ValueError(f"PNG canvas changed: expected {expected}, obtained {png_size}")
            metadata = {
                "schema_version": "figure-render-v1", "figure_id": spec["id"],
                "id": spec["id"], "source": spec["source"], "canvas": spec["canvas"],
                "data_provenance": spec.get("data", {}).get("provenance", []),
                "uncertainties": spec.get("uncertainties", []),
                "label_background": label_policy,
                "style_version": style["version"], "style_override": spec.get("style_override", {}),
                "spec_sha256": sha256(spec_path), "style_sha256": sha256(style_path),
                "script_sha256": sha256(script_path), "exporter_sha256": sha256(__file__),
                "matplotlib_version": matplotlib.__version__, "png_dpi": DPI, "dpi": DPI,
                "png_size_px": png_size, "png_rounding": "truncate subpixel canvas dimensions, below one pixel",
                "vector_kind": vector_kind, "rasterization": raster_reasons,
                "vector_inspection_limit": "Artist inspection; inspect exported PDF/SVG and compare visually before acceptance.",
                "pdf_fonttype": selected_fonttype, "font_export_policy": font_export, "svg_fonttype": "path",
                "fonts": [{"path": name, "sha256": sha256(name)} for name in sorted(fonts)],
                "files": files,
                "artifacts": [{"path": item["file"], "format": ext, "sha256": item["sha256"],
                               "declared_content": "raster" if ext == "png" else ("mixed" if raster_reasons else "vector")}
                              for ext, item in files.items()],
                "skipped_vector_formats": skipped,
                "acceptance": "unreviewed",
            }
            (output / "render-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return metadata
        finally:
            plt.close(fig)
