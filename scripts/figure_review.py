#!/usr/bin/env python3
"""Read-only structural checks and visual comparison; never semantic acceptance.

The source is uniformly resampled and letterboxed, never warped or registered
to the candidate. Edge measurements are diagnostics, not acceptance thresholds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

DPI = 400


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rgb(image):
    image = image.convert("RGBA")
    background = Image.new("RGBA", image.size, "white")
    return Image.alpha_composite(background, image).convert("RGB")


def valid_spec(spec):
    if not isinstance(spec.get("id"), str) or not spec["id"]:
        raise ValueError("spec.id must be a non-empty string")
    canvas, source = spec["canvas"], spec["source"]
    for key in ("width_mm", "height_mm"):
        value = canvas[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid canvas.{key}")
    box, page = source["bbox_px"], source["page_size_px"]
    if len(box) != 4 or len(page) != 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in [*box, *page]):
        raise ValueError("Source coordinates must be finite numbers")
    if not (0 <= box[0] < box[2] <= page[0] and 0 <= box[1] < box[3] <= page[1]):
        raise ValueError("Invalid source bbox_px/page_size_px")


def artifacts(meta):
    result = []
    alias_rows = meta.get("artifacts", [])
    aliases = {row["format"]: row for row in alias_rows}
    if len(aliases) != len(alias_rows):
        raise ValueError("Duplicate artifact formats")
    for kind, row in meta.get("files", {}).items():
        alias = aliases.pop(kind, {})
        if alias and (alias.get("path") != row["file"] or alias.get("sha256") != row.get("sha256")):
            raise ValueError(f"Inconsistent files/artifacts declarations for {kind}")
        result.append({"format": kind, "path": row["file"], "sha256": row.get("sha256"),
                       "declared_content": alias.get("declared_content", "raster" if kind == "png" else
                                                     "vector" if meta.get("vector_kind") == "vector_artists" else "mixed")})
    result.extend(aliases.values())
    if len({row["format"] for row in result}) != len(result):
        raise ValueError("Duplicate artifact formats")
    return result


def inspect_pdf(path, canvas):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    with fitz.open(path) as doc:
        if not doc.is_pdf:
            raise ValueError("Declared PDF artifact is not a PDF document")
        pages = []
        for page in doc:
            box = page.mediabox
            image_objects = page.get_images(full=True)
            displayed = page.get_image_info(xrefs=True)
            drawings = page.get_drawings()
            text = page.get_text("dict")
            spans = sum(len(line.get("spans", [])) for block in text["blocks"] if block["type"] == 0 for line in block["lines"])
            has_images = bool(image_objects or displayed)
            has_vector = bool(drawings or spans)
            kind = "mixed" if has_images and has_vector else "raster" if has_images else "vector" if has_vector else "empty"
            pages.append({"mediabox_pt": [box.width, box.height], "rotation": page.rotation,
                          "image_xobjects": len(image_objects), "displayed_images": len(displayed),
                          "inline_images": sum(row.get("xref", 0) == 0 for row in displayed),
                          "drawing_paths": len(drawings), "text_spans": spans, "actual_content": kind})
        expected = [canvas[key] / 25.4 * 72 for key in ("width_mm", "height_mm")]
        return {"pages": pages, "page_count": len(doc), "expected_mediabox_pt": expected,
                "size_ok": len(doc) == 1 and all(abs(a - b) <= 0.05 for a, b in zip(pages[0]["mediabox_pt"], expected)),
                "actual_content": pages[0]["actual_content"] if len(doc) == 1 else "multiple_pages",
                "inspection_limit": "Structural resources/paths/text, not visible semantic correctness; includes listed unused image resources."}


def inspect_svg(path):
    root = ET.parse(path).getroot()
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("Declared SVG artifact has no svg root element")
    tags = [element.tag.rsplit("}", 1)[-1] for element in root.iter()]
    images = sum(tag in {"image", "feImage"} for tag in tags)
    foreign = sum(tag == "foreignObject" for tag in tags)
    vectors = sum(tag in {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "use"} for tag in tags)
    kind = "mixed" if (images or foreign) and vectors else "raster" if images or foreign else "vector" if vectors else "empty"
    return {"image_elements": images, "foreign_objects": foreign, "vector_elements": vectors,
            "actual_content": kind, "inspection_limit": "XML element check; foreignObject conservatively treated as non-vector."}


def uniform_canvas(image, size):
    scale = min(size[0] / image.width, size[1] / image.height)
    resized = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
    offset = ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2)
    output = Image.new("RGB", size, "white")
    output.paste(resized, offset)
    return output, {"uniform_scale": scale, "padding_offset_px": list(offset), "resampled_size_px": list(resized.size),
                    "rotation": 0, "registration": "none", "geometry": "uniform resampling and letterboxing only"}


def edges(image):
    gray = np.asarray(image.convert("L"))
    try:
        import cv2
        return cv2.Canny(gray, 80, 160) > 0, "OpenCV Canny(80,160)"
    except ImportError:
        filtered = np.asarray(image.convert("L").filter(ImageFilter.FIND_EDGES))
        result = filtered > 40
        result[[0, -1], :] = False
        result[:, [0, -1]] = False
        return result, "Pillow FIND_EDGES threshold 40"


def diagnostics(source, candidate):
    source_edges, method = edges(source)
    candidate_edges, _ = edges(candidate)
    result = {"edge_method": method, "edge_pixels": {"source": int(source_edges.sum()), "candidate": int(candidate_edges.sum())},
              "ink_fraction_below_gray_200": {"source": float((np.asarray(source.convert("L")) < 200).mean()),
                                               "candidate": float((np.asarray(candidate.convert("L")) < 200).mean())},
              "acceptance_threshold": None, "interpretation": "Diagnostic only; scan noise, fonts, antialiasing and margins affect these numbers."}
    try:
        import cv2
        if source_edges.any() and candidate_edges.any():
            distances = {}
            for name, measured, reference in (("source_to_candidate", source_edges, candidate_edges), ("candidate_to_source", candidate_edges, source_edges)):
                distance = cv2.distanceTransform((~reference).astype(np.uint8), cv2.DIST_L2, 5)[measured]
                distances[name] = {"mean_px": float(distance.mean()), "p95_px": float(np.quantile(distance, .95))}
            result["edge_distance_at_400dpi"] = distances
    except ImportError:
        result["edge_distance_at_400dpi"] = "not_computed: OpenCV unavailable"
    rgba = np.zeros((*source_edges.shape, 4), dtype=np.uint8)
    rgba[source_edges] = [220, 55, 45, 170]
    rgba[candidate_edges] = [30, 100, 235, 170]
    rgba[source_edges & candidate_edges] = [150, 45, 165, 210]
    return result, Image.fromarray(rgba, "RGBA")


def review(spec_path, meta_path, source_path, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output}")
    spec_path, meta_path, source_path = map(lambda path: Path(path).resolve(), (spec_path, meta_path, source_path))
    spec, meta = (json.loads(path.read_text(encoding="utf-8")) for path in (spec_path, meta_path))
    valid_spec(spec)
    retain_source = meta.get("export_mode") == "retain_source"
    width, height = (spec["canvas"][key] for key in ("width_mm", "height_mm"))
    size = (round(width / 25.4 * DPI), round(height / 25.4 * DPI))
    box = spec["source"].get("view_bbox_px", spec["source"]["bbox_px"])
    if len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box) or not (box[0] < box[2] and box[1] < box[3]):
        raise ValueError("Invalid source.view_bbox_px")
    native_size = [round(box[2] - box[0]), round(box[3] - box[1])]
    native_dpi = [native_size[0] / width * 25.4, native_size[1] / height * 25.4]
    if min(size) < 1 or max(size) > 20000 or size[0] * size[1] > 100_000_000:
        raise ValueError("Canvas exceeds the review tool's image size limit")
    failures, warnings, checks = [], [], []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            failures.append(name)

    check("figure_id", meta.get("figure_id", meta.get("id")) == spec["id"], meta.get("figure_id", meta.get("id")))
    check("spec_sha256", meta.get("spec_sha256") == digest(spec_path), "Exact frozen spec file digest")
    check("metadata_canvas", all(meta.get("canvas", {}).get(key) == spec["canvas"][key] for key in ("width_mm", "height_mm")), meta.get("canvas"))
    if retain_source:
        check("retain_vector_kind", meta.get("vector_kind") == "source_raster", meta.get("vector_kind"))
        for key in ("png_dpi", "effective_dpi"):
            value = meta.get(key)
            values = value if isinstance(value, (list, tuple)) else [value, value]
            check(f"retained_{key}", len(values) == 2 and all(isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a) and abs(a - b) <= .1 for a, b in zip(values, native_dpi)),
                  {"declared": value, "effective_xy": native_dpi, "tolerance_dpi": .1})
        warnings.append("Retained source PNG preserves native pixels; it is not upsampled to 400 dpi and contains no newly recovered detail. Comparison previews may be resampled for display only.")
    else:
        check("declared_png_dpi", meta.get("png_dpi", meta.get("dpi")) == DPI, meta.get("png_dpi", meta.get("dpi")))
    check("source_geometry_metadata", meta.get("source") == spec.get("source"), "Render source must match frozen spec")
    results, candidate = [], None
    root = meta_path.parent
    for row in artifacts(meta):
        path = (root / row["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Artifact paths must stay inside render-meta directory")
        kind, declared = row["format"], row.get("declared_content")
        result = {"format": kind, "path": str(path), "declared_content": declared}
        results.append(result)
        check(f"{kind}_exists", path.is_file(), str(path))
        if not path.is_file():
            continue
        result["sha256"] = digest(path)
        check(f"{kind}_sha256", result["sha256"] == row.get("sha256"), "Actual file hash matches declaration")
        try:
            if kind == "png":
                with Image.open(path) as image:
                    check("png_format", image.format == "PNG", image.format)
                    result.update({"size_px": list(image.size), "dpi": list(image.info.get("dpi", [])), "actual_content": "raster"})
                    if retain_source:
                        check("retained_png_native_size", list(image.size) == native_size, {"actual": list(image.size), "expected_native": native_size})
                        check("retained_png_source_sha256", result["sha256"] == spec["source"].get("crop_sha256"), "Retained PNG bytes must equal the frozen source.crop_sha256")
                        result["effective_dpi"] = [image.width / width * 25.4, image.height / height * 25.4]
                        result["dpi_metadata_policy"] = "Preserve source bytes; physical size comes from frozen canvas, not inherited PNG density tags"
                    else:
                        check("png_size_400dpi", all(abs(actual - expected) <= 1 for actual, expected in zip(image.size, size)), {"actual": list(image.size), "expected": list(size), "tolerance_px": 1})
                        check("png_dpi_metadata", len(result["dpi"]) == 2 and all(abs(value - DPI) <= .1 for value in result["dpi"]), result["dpi"])
                    candidate = rgb(image)
            elif kind == "pdf":
                result.update(inspect_pdf(path, spec["canvas"]))
                check("pdf_mediabox", result["size_ok"], result["pages"])
                check("pdf_rotation", all(page["rotation"] == 0 for page in result["pages"]), "Exported page must not add a page rotation")
            elif kind == "svg":
                result.update(inspect_svg(path))
            else:
                check(f"{kind}_supported", False, "Supported formats: png, pdf, svg")
                continue
            check(f"{kind}_declared_content", declared in {"vector", "mixed", "raster"} and declared == result["actual_content"], {"declared": declared, "observed": result["actual_content"]})
            if retain_source:
                check(f"retained_{kind}_not_vector", result["actual_content"] in {"raster", "mixed"}, "A retained pixel image cannot be declared a pure vector artifact")
        except Exception as error:
            check(f"{kind}_readable", False, f"{type(error).__name__}: {error}")
    check("required_png", candidate is not None, "Native source PNG is required" if retain_source else "400 dpi PNG is required for visual review")
    with Image.open(source_path) as image:
        source = rgb(image)
    expected_crop = [box[2] - box[0], box[3] - box[1]]
    check("source_crop_size", list(source.size) == native_size if retain_source else all(abs(actual - expected) <= 2 for actual, expected in zip(source.size, expected_crop)), {"actual": list(source.size), "bbox_extent": expected_crop, "coordinate_frame": "view_bbox_px" if "view_bbox_px" in spec["source"] else "bbox_px", "tolerance_px": 0 if retain_source else 2})
    if "crop_sha256" in spec["source"]:
        check("source_crop_sha256", digest(source_path) == spec["source"]["crop_sha256"], "Prepared source crop matches frozen spec")
    else:
        if retain_source:
            check("retained_source_frozen_hash", False, "retain_source requires source.crop_sha256")
        else:
            warnings.append("Spec has no source.crop_sha256; crop dimensions are checked and input digest recorded, but frozen provenance is not authenticated.")
    normalized_source, geometry = uniform_canvas(source, size)
    if abs(source.width / source.height - size[0] / size[1]) / (source.width / source.height) > .01:
        warnings.append("Source crop and canvas aspect ratios differ by more than 1%; source is letterboxed, never stretched. Review the frozen bbox/canvas.")
    output.mkdir(parents=True, exist_ok=False)
    visual_files, metrics = {}, None
    if candidate is not None:
        normalized_candidate, candidate_geometry = uniform_canvas(candidate, size)
        if candidate.size != size:
            warnings.append("Candidate preview uniformly resampled to the target canvas; original PNG dimensions remain independently checked.")
        band = 42
        side = Image.new("RGB", (size[0] * 2 + 24, size[1] + band), "#eeeeee")
        side.paste(normalized_source, (0, band))
        side.paste(normalized_candidate, (size[0] + 24, band))
        draw = ImageDraw.Draw(side)
        draw.text((12, 12), "SOURCE / same physical canvas", fill="black")
        draw.text((size[0] + 36, 12), "CANDIDATE / same physical canvas", fill="black")
        side.save(output / "side-by-side.png", dpi=(DPI, DPI))
        metrics, overlay = diagnostics(normalized_source, normalized_candidate)
        overlay.save(output / "edges-overlay.png", dpi=(DPI, DPI))
        preview = Image.alpha_composite(Image.new("RGBA", size, "white"), overlay)
        preview.save(output / "edges-overlay-white.png", dpi=(DPI, DPI))
        visual_files = {name: {"path": name, "sha256": digest(output / name)} for name in ("side-by-side.png", "edges-overlay.png", "edges-overlay-white.png")}
        ink = np.asarray(normalized_candidate.convert("L")) < 200
        border = np.concatenate((ink[0], ink[-1], ink[:, 0], ink[:, -1]))
        if border.any():
            warnings.append("Candidate ink touches the outer canvas edge; inspect clipping or intentional bleed. This is a diagnostic, not an automatic failure.")
        if not ink.any():
            warnings.append("Candidate appears blank under a gray<200 diagnostic; faint content still needs visual review.")
    else:
        candidate_geometry = None
    required = ["labels", "units", "axis_data", "nodes_and_edges", "line_dashes", "geometry", "style"]
    report = {"schema_version": 1, "figure_id": spec["id"], "technical_pass": not failures,
              "semantic_accuracy": "not_evaluated", "visual_acceptance": "unreviewed",
              "required_independent_review": [{"aspect": name, "status": "not_evaluated"} for name in required],
              "text_completeness": "not_evaluated: no reliable label inventory/OCR comparison in this tool",
              "inputs": {"spec": str(spec_path), "spec_sha256": digest(spec_path), "render_meta": str(meta_path),
                         "render_meta_sha256": digest(meta_path), "source_crop": str(source_path), "source_crop_sha256": digest(source_path)},
              "canvas": spec["canvas"], "export_mode": "retain_source" if retain_source else "reconstruction",
              "target_dpi": native_dpi if retain_source else DPI, "target_size_px": native_size if retain_source else list(size),
              "comparison_preview_dpi": DPI, "comparison_preview_size_px": list(size), "checks": checks,
              "failed_checks": failures, "warnings": warnings, "artifacts": results,
              "source_preparation_transform": spec["source"].get("page_transform"),
              "source_preview_transform": geometry, "candidate_preview_transform": candidate_geometry,
              "comparison_files": visual_files, "edge_overlay_legend": {"source_only": "red", "candidate_only": "blue", "overlap": "purple"},
              "diagnostics": metrics, "limitations": ["Technical pass does not imply faithful reconstruction or readability.",
                "No source rotation, deformation correction, semantic OCR, label matching or image registration is performed.",
                "Source crop provenance is recorded; its pixels cannot be authenticated against the PDF without the original PDF input.",
                "PDF/SVG structural classification is conservative and includes invisible/unused resource elements."]}
    (output / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--render-meta", required=True, type=Path)
    parser.add_argument("--source-crop", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New directory; existing directories are rejected")
    args = parser.parse_args()
    try:
        result = review(args.spec, args.render_meta, args.source_crop, args.output)
    except (ValueError, KeyError, OSError, ET.ParseError) as error:
        parser.exit(2, f"figure_review: {error}\n")
    print(json.dumps({"technical_pass": result["technical_pass"], "semantic_accuracy": result["semantic_accuracy"],
                      "report": str(args.output.resolve() / "review.json")}, ensure_ascii=False))
    return 0 if result["technical_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
