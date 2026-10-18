#!/usr/bin/env python3
"""Compare an independently reviewed source inventory with semantic figures.

This is a coverage gate, not an image detector or a visual correctness metric.
The inventory must be prepared from source pages before reading OCR figure
lists. Explicit reviewed pages include pages on which no illustration exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from asset_contract import resolve_asset
from source_identity import source_digest


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def overlap(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, x1 - x0) * max(0, y1 - y0)
    return intersection / ((a[2] - a[0]) * (a[3] - a[1]))


def figure_page(figure):
    return figure.get('physical_page', figure.get('page', figure.get('source', {}).get('page')))


def audit(source_pdf, inventory_path, blocks_path, figures_path, covers_path=None):
    from PIL import Image

    inventory = load_json(inventory_path)
    registry = load_json(figures_path) if figures_path else {'source_sha256': source_digest(source_pdf), 'figures': []}
    blocks = [json.loads(line) for line in Path(blocks_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    source_hash = source_digest(source_pdf)
    errors, rows = [], []
    if inventory.get("source_sha256") != source_hash or registry.get("source_sha256") != source_hash:
        errors.append("Source PDF, inventory and figure registry hashes differ")
    scope = inventory.get("scope_pages", [])
    covers = {}
    if covers_path:
        from prepare_covers import load_cover_manifest
        covers = load_cover_manifest(covers_path, source_hash, scope)
    reviews = inventory.get("reviewed_pages", [])
    if not scope or len(set(scope)) != len(scope) or any(type(p) is not int or p < 1 for p in scope):
        errors.append("scope_pages must be distinct positive physical page numbers")
    if inventory.get("method") != "source_first_visual_inventory":
        errors.append("Inventory must declare source_first_visual_inventory; an OCR-only inventory is insufficient")
    if {r.get("physical_page") for r in reviews} != set(scope) or len(reviews) != len(scope):
        errors.append("Every scope page must have exactly one review, including pages with no figures")
    review_map = {r.get("physical_page"): r for r in reviews}
    sizes = {}
    for page, review in review_map.items():
        path = Path(review.get("source_image", ""))
        if not review.get("reviewer") or not review.get("viewed_evidence"):
            errors.append(f"Page {page} lacks a reviewer or actually viewed evidence")
        if not path.is_file() or sha256(path) != review.get("source_image_sha256"):
            errors.append(f"Page {page} source image missing or hash mismatch")
            continue
        with Image.open(path) as im:
            sizes[page] = im.size
    by_id = {b.get("block_id", b.get("id")): b for b in blocks}
    registry_figures = registry.get("figures", [])
    figures = {f["figure_id"]: f for f in registry_figures}
    if len(figures) != len(registry_figures):
        errors.append("Duplicate registry figure_id")
    claimed, semantic_ids = set(), set()
    expected_markers = {}
    items = inventory.get("items", [])
    if len({i.get("item_id") for i in items}) != len(items):
        errors.append("Duplicate inventory item_id")
    for item in items:
        item_id, page, box = item.get("item_id"), item.get("physical_page"), item.get("bbox_px")
        item_errors = []
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
            item_errors.append("Invalid bbox_px")
        elif page not in sizes or not (0 <= box[0] < box[2] <= sizes[page][0] and 0 <= box[1] < box[3] <= sizes[page][1]):
            item_errors.append("bbox outside reviewed source image")
        if not item.get("description") or page not in scope:
            item_errors.append("Missing description or page outside scope")
        block_id = item.get("source_block_id")
        block = by_id.get(block_id)
        if page in covers:
            # The complete native page covers all its artwork. Do not demand
            # element OCR or redundant logo/decorative assets in the clean book.
            cover = covers[page]
            rows.append({"item_id": item_id, "page": page, "kind": item.get('kind'),
                         "source_block_id": block_id, "figure_id": item.get('figure_id'),
                         "coverage_pass": not item_errors, "errors": item_errors,
                         "rendered_as": 'native_whole_page_cover' if cover['mode'] == 'image' else 'user_selected_metadata_cover',
                         "cover_role": cover['role'], "cover_manifest_sha256": sha256(covers_path)})
            errors.extend(f"{item_id}: {e}" for e in item_errors)
            continue
        if block is None:
            item_errors.append("Source item has no semantic block: OCR may have omitted it")
        elif block.get("page") != page or block.get("source_sha256") != source_hash:
            item_errors.append("Semantic block page/source mismatch")
        elif not item_errors and overlap(box, block.get("bbox", [0, 0, 0, 0])) < 0.5:
            item_errors.append("Less than half of the independently located source item overlaps its semantic block")
        kind = item.get("kind")
        if kind == "table_inline_graphic":
            if block and block.get("type") != "table":
                item_errors.append("Table graphic bound to a non-table block")
            marker = item.get("semantic_marker")
            if not isinstance(marker, str) or not marker or (block and marker not in block.get("text", "")):
                item_errors.append("Required table graphic marker is absent from semantic table")
            elif block:
                key = (block_id, marker)
                expected_markers[key] = expected_markers.get(key, 0) + 1
        elif kind in ("figure", "decoration", "qr"):
            figure_id = item.get("figure_id")
            figure = figures.get(figure_id)
            if figure_id in claimed:
                item_errors.append("Multiple independent source items bound to one figure asset")
            if block and (block.get("type") != "figure" or block.get("figure_id") != figure_id):
                item_errors.append("Inventory item is missing or misclassified in semantic figures")
            if figure is None or figure_page(figure) != page:
                item_errors.append("No matching figure asset record")
            else:
                claimed.add(figure_id)
                asset = figure.get("final_asset") or figure.get("final_assets", {}).get("latex")
                try:
                    resolve_asset(asset, Path(figures_path).parent)
                except (ValueError, TypeError):
                    item_errors.append("Selected final figure asset missing")
            semantic_ids.add(block_id)
        else:
            item_errors.append("Unsupported inventory kind")
        rows.append({"item_id": item_id, "page": page, "kind": kind, "source_block_id": block_id,
                     "figure_id": item.get("figure_id"), "coverage_pass": not item_errors, "errors": item_errors})
        errors.extend(f"{item_id}: {e}" for e in item_errors)
    marker_counts = []
    for (block_id, marker), expected in expected_markers.items():
        actual = by_id[block_id].get("text", "").count(marker)
        marker_counts.append({"block_id": block_id, "marker": marker, "source_count": expected,
                              "semantic_count": actual, "count_pass": actual >= expected})
        if actual < expected:
            errors.append(f"{block_id}: only {actual} markers {marker!r} for {expected} independently reviewed source graphics")
    undeclared_assets = sorted(f["figure_id"] for f in figures.values() if figure_page(f) in scope and figure_page(f) not in covers and f["figure_id"] not in claimed)
    unreviewed_blocks = sorted(b.get("block_id", b.get("id")) for b in blocks if b.get("page") in scope and b.get('page') not in covers and b.get("type") == "figure" and b.get("block_id", b.get("id")) not in semantic_ids)
    if undeclared_assets:
        errors.append("Assets absent from independent source inventory: " + ", ".join(undeclared_assets))
    if unreviewed_blocks:
        errors.append("Semantic figures absent from independent source inventory: " + ", ".join(unreviewed_blocks))
    return {"schema_version": 1, "source_sha256": source_hash, "scope_pages": scope,
            "inventory_sha256": sha256(inventory_path), "blocks_sha256": sha256(blocks_path),
            "figures_sha256": sha256(figures_path) if figures_path else None, "reviewed_page_count": len(reviews),
            "cover_pages": sorted(covers), "covers_sha256": sha256(covers_path) if covers_path else None,
            "source_inventory_item_count": len(items), "items": rows, "table_graphic_marker_counts": marker_counts,
            "unreviewed_figure_blocks": unreviewed_blocks, "undeclared_assets": undeclared_assets,
            "coverage_pass": not errors, "errors": errors,
            "visual_accuracy": "not_automatically_evaluated",
            "limits": "Coverage requires an honest source-first visual inventory. Hashes and semantic markers do not prove that the final PDF visibly contains the correct graphics; render it for a separate visual check."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pdf", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--blocks", required=True)
    parser.add_argument("--figures", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--covers", help="Reviewed cover manifest, replacing element reconstruction")
    args = parser.parse_args()
    result = audit(args.source_pdf, args.inventory, args.blocks, args.figures, args.covers)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"coverage_pass": result["coverage_pass"], "items": result["source_inventory_item_count"], "errors": result["errors"]}, ensure_ascii=False))
    raise SystemExit(0 if result["coverage_pass"] else 2)


if __name__ == "__main__":
    main()
