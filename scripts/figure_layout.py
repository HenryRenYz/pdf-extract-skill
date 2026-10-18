#!/usr/bin/env python3
"""Validate explicit source-reviewed figure layouts and plan indivisible groups.

No text is edited or split, and no adjacent figures are inferred automatically.
The exporter may use non-floating minipages for accepted groups. An uncertain
or oversized text/figure composition falls back to ordinary source order.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def digest_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_ids(block):
    return [s["block_id"] for s in block.get("sources", [])] or [block.get("block_id", block.get("id"))]


def build_layout_plan(blocks, metadata, *, text_width_mm=164.0,
                      minimum_text_width_mm=55.0, max_text_characters=450, figure_scale=1.0):
    """Return groups, by_anchor, consumed_ids, warnings and coverage.

    Groups contain exact semantic block IDs (including captions), per-figure
    widths from frozen native bbox/page width, source-order IDs and an anchor.
    consumed_ids includes anchors; a caller renders by_anchor first and skips
    other consumed IDs. Rejected structural contracts raise ValueError;
    source-reviewed groups too wide/long are omitted with a warning instead.
    """
    if not math.isfinite(text_width_mm) or text_width_mm <= 0:
        raise ValueError("text_width_mm must be finite and positive")
    if not math.isfinite(figure_scale) or figure_scale <= 0:
        raise ValueError("figure_scale must be finite and positive")
    ids = [b.get("block_id", b.get("id")) for b in blocks]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate semantic block ID")
    by_id, positions = dict(zip(ids, blocks)), {bid: i for i, bid in enumerate(ids)}
    source_hash = metadata.get("source_sha256")
    if not source_hash or any(b.get("source_sha256") != source_hash for b in blocks):
        raise ValueError("Layout metadata does not belong to this book source")
    groups, warnings, consumed = [], [], set()
    seen_group_ids = set()
    for decision in metadata.get("groups", []):
        gid, kind = decision.get("group_id"), decision.get("kind")
        if not gid or gid in seen_group_ids:
            raise ValueError("Missing/duplicate layout group_id")
        seen_group_ids.add(gid)
        review = decision.get("review", {})
        if review.get("source_checked") is not True or not review.get("reviewer") or not review.get("evidence"):
            raise ValueError(f"{gid}: explicit actual-source review is required")
        pairs = decision.get("figures", [])
        if (kind == "figure_row" and len(pairs) < 2) or (kind == "text_figure" and len(pairs) != 1):
            raise ValueError(f"{gid}: wrong number of figures")
        if kind not in ("figure_row", "text_figure"):
            raise ValueError(f"{gid}: unsupported layout kind")
        member_ids, descriptors = [], []
        page = decision.get("physical_page")
        for pair in pairs:
            fid, cid = pair.get("block_id"), pair.get("caption_block_id")
            if fid not in by_id or cid not in by_id:
                raise ValueError(f"{gid}: figure/caption missing")
            figure, caption = by_id[fid], by_id[cid]
            selected = figure.get("figure", {})
            if review.get("source_image_sha256") != figure.get("source_image_sha256"):
                raise ValueError(f"{gid}: reviewed source page image hash mismatch")
            if (figure.get("type") != "figure" or caption.get("type") != "caption" or
                    figure.get("page") != page or caption.get("page") != page or
                    figure.get("figure_id") != pair.get("figure_id") or
                    selected.get("caption_block_id") != cid):
                raise ValueError(f"{gid}: incorrect source figure/caption binding")
            if selected.get("source_sha256") != source_hash:
                raise ValueError(f"{gid}: figure belongs to another source")
            box, expected = selected.get("bbox_px"), pair.get("bbox_px")
            canvas = selected.get("canvas", {})
            nominal = canvas.get("nominal_page_width_px")
            if (not isinstance(box, list) or len(box) != 4 or box != expected or
                    type(nominal) not in (int, float) or nominal <= 0 or canvas.get("frozen") is not True):
                raise ValueError(f"{gid}: figure bbox/scale differs from reviewed frozen canvas")
            if caption.get("text") != pair.get("source_caption"):
                raise ValueError(f"{gid}: caption text changed since source review")
            if (pair.get("figure_source_ids", source_ids(figure)) != source_ids(figure) or
                    pair.get("caption_source_ids", source_ids(caption)) != source_ids(caption)):
                raise ValueError(f"{gid}: figure/caption source mapping changed")
            descriptors.append({"figure_id": pair["figure_id"], "block_id": fid,
                                "caption_block_id": cid, "source_bbox_px": box,
                                "figure_source_ids": source_ids(figure),
                                "caption_source_ids": source_ids(caption),
                                "width_mm": (box[2] - box[0]) / nominal * text_width_mm * figure_scale,
                                "height_mm": (box[3] - box[1]) / nominal * text_width_mm * figure_scale,
                                "nominal_page_width_px": nominal,
                                "caption_text_sha256": digest_text(caption["text"])})
            member_ids.extend([fid, cid])
        if kind == "figure_row":
            if descriptors != sorted(descriptors, key=lambda f: f["source_bbox_px"][0]):
                raise ValueError(f"{gid}: row must preserve source left-to-right reading order")
            for left, right in zip(descriptors, descriptors[1:]):
                a, b = left["source_bbox_px"], right["source_bbox_px"]
                if a[2] > b[0] or min(a[3], b[3]) <= max(a[1], b[1]):
                    raise ValueError(f"{gid}: source figures do not form a disjoint horizontal band")
                if left["nominal_page_width_px"] != right["nominal_page_width_px"]:
                    raise ValueError(f"{gid}: inconsistent page-scale basis")
            gaps = [(right["source_bbox_px"][0] - left["source_bbox_px"][2]) /
                    left["nominal_page_width_px"] * text_width_mm
                    for left, right in zip(descriptors, descriptors[1:])]
            total_width = sum(f["width_mm"] for f in descriptors) + sum(gaps)
            if total_width > text_width_mm:
                warnings.append({"group_id": gid, "reason": "source row exceeds text width; ordinary sequential layout retained"})
                continue
            layout = {"gap_widths_mm": gaps, "total_width_mm": total_width}
        else:
            records = decision.get("text_blocks")
            if records is None:
                records = [{"block_id": decision.get("text_block_id"),
                            "text_sha256": decision.get("text_sha256"),
                            "source_block_ids": decision.get("text_source_block_ids"),
                            "source_bbox_px": decision.get("source_text_bbox_px"),
                            "reviewed_text_sources": decision.get("reviewed_text_sources", [])}]
            if not isinstance(records, list) or not records:
                raise ValueError(f"{gid}: complete adjacent text blocks are required")
            text_ids, text_boxes, texts = [], [], []
            for record in records:
                tid = record.get("block_id")
                text = by_id.get(tid)
                if not text or text.get("type") not in {"paragraph", "formula", "example", "proof", "theorem"}:
                    raise ValueError(f"{gid}: complete adjacent body/formula blocks are required")
                if (digest_text(text.get("text", "")) != record.get("text_sha256") or
                        source_ids(text) != record.get("source_block_ids")):
                    raise ValueError(f"{gid}: text or merged continuation changed since source review")
                reviewed_sources = {(s.get("page"), s.get("source_image_sha256"))
                                    for s in record.get("reviewed_text_sources", [])}
                own_sources = text.get("sources", []) or [text]
                for source in own_sources:
                    if source.get("page") == page:
                        if source.get("source_image_sha256") != review.get("source_image_sha256"):
                            raise ValueError(f"{gid}: text source page image hash mismatch")
                    elif (source.get("page"), source.get("source_image_sha256")) not in reviewed_sources:
                        raise ValueError(f"{gid}: every continuation source page needs review")
                own_source = next((s for s in own_sources if s.get("page") == page), None)
                source_box = record.get("source_bbox_px")
                if (not isinstance(source_box, list) or len(source_box) != 4 or
                        any(type(v) not in (int, float) or not math.isfinite(v) for v in source_box) or
                        source_box[2] <= source_box[0] or source_box[3] <= source_box[1] or
                        own_source is None or own_source.get("bbox") != source_box):
                    raise ValueError(f"{gid}: source text geometry changed")
                text_ids.append(tid)
                text_boxes.append(source_box)
                texts.append(text)
            if text_ids != sorted(text_ids, key=positions.__getitem__):
                raise ValueError(f"{gid}: text blocks must retain semantic order")
            if decision.get("text_block_ids", text_ids) != text_ids:
                raise ValueError(f"{gid}: text block list differs from complete descriptors")
            box = [min(b[0] for b in text_boxes), min(b[1] for b in text_boxes),
                   max(b[2] for b in text_boxes), max(b[3] for b in text_boxes)]
            if decision.get("source_text_bbox_px") != box:
                raise ValueError(f"{gid}: text bbox must cover all complete reviewed blocks")
            fbox = descriptors[0]["source_bbox_px"]
            side = decision.get("source_position", decision.get("figure_side"))
            if side not in ("left", "right") or any((side == "left" and fbox[2] > b[0]) or (side == "right" and b[2] > fbox[0]) for b in text_boxes):
                raise ValueError(f"{gid}: source does not support the declared figure side")
            if min(fbox[3], box[3]) <= max(fbox[1], box[1]):
                raise ValueError(f"{gid}: text and figure have no overlapping horizontal band")
            gap_px = box[0] - fbox[2] if side == "left" else fbox[0] - box[2]
            gap_mm = gap_px / descriptors[0]["nominal_page_width_px"] * text_width_mm
            text_mm = text_width_mm - descriptors[0]["width_mm"] - gap_mm
            render_side = decision.get("render_position", side)
            override = decision.get("user_authorized_layout_override")
            if render_side not in ("left", "right"):
                raise ValueError(f"{gid}: invalid render_position")
            if render_side != side and (not isinstance(override, dict) or override.get("authorized") is not True or not override.get("instruction")):
                raise ValueError(f"{gid}: changing source-side arrangement requires explicit user authorization")
            if text_mm < minimum_text_width_mm or sum(len(t["text"]) for t in texts) > max_text_characters or any(t.get("footnotes") for t in texts):
                warnings.append({"group_id": gid, "reason": "paragraph too long/narrow or has footnotes; complete text and figure retained in ordinary sequence"})
                continue
            member_ids.extend(text_ids)
            layout = {"figure_side": render_side, "source_position": side, "render_position": render_side,
                      "user_authorized_layout_override": override,
                      "text_block_id": text_ids[0], "text_block_ids": text_ids, "text_width_mm": text_mm,
                      "gap_width_mm": gap_mm, "text_blocks": records,
                      "text_source_block_ids": [sid for t in texts for sid in source_ids(t)],
                      "text_sha256": {tid: digest_text(by_id[tid]["text"]) for tid in text_ids}}
        indices = sorted(positions[bid] for bid in member_ids)
        if len(set(member_ids)) != len(member_ids) or set(range(indices[0], indices[-1] + 1)) != set(indices):
            raise ValueError(f"{gid}: grouping would cross an intervening block, heading, footer or unconsumed content")
        if consumed.intersection(member_ids):
            raise ValueError(f"{gid}: block consumed by multiple layout groups")
        ordered = [ids[i] for i in indices]
        group = {"group_id": gid, "kind": kind, "anchor_id": ordered[0], "member_ids": ordered,
                 "figures": descriptors, "physical_page": page, "source_sha256": source_hash,
                 "review": review, "non_floating": True, "figure_scale": figure_scale,
                 "fallback_on_page_height_exceeded": "review_plan_and_reexport_complete_blocks",
                 "must_measure_rendered_height": True, **layout}
        groups.append(group)
        consumed.update(member_ids)
    by_anchor = {g["anchor_id"]: g for g in groups}
    expansion = []
    for bid in ids:
        if bid in by_anchor:
            expansion.extend(by_anchor[bid]["member_ids"])
        elif bid not in consumed:
            expansion.append(bid)
    if Counter(expansion) != Counter(ids):
        raise ValueError("Layout plan duplicates or omits semantic blocks")
    input_sources = [sid for b in blocks for sid in source_ids(b)]
    planned_sources = [sid for bid in expansion for sid in source_ids(by_id[bid])]
    duplicate_sources = sorted(s for s, n in Counter(input_sources).items() if n > 1)
    if duplicate_sources:
        raise ValueError("Input book already duplicates source provenance: " + ", ".join(duplicate_sources))
    if Counter(input_sources) != Counter(planned_sources):
        raise ValueError("Layout plan duplicates or omits source provenance")
    return {"schema_version": 1, "groups": groups, "by_anchor": by_anchor,
            "consumed_ids": sorted(consumed), "warnings": warnings,
            "source_coverage": {"semantic_block_count": len(ids), "planned_block_count": len(expansion),
                                "source_reference_count": len(input_sources), "unchanged": True,
                                "exactly_once": True},
            "limits": "Exact block/source consumption is checked. Actual minipage height, glyph visibility and rendered layout still require PDF visual verification."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--text-width-mm", type=float, default=164.0)
    args = parser.parse_args()
    blocks = [json.loads(line) for line in Path(args.blocks).read_text(encoding="utf-8").splitlines() if line.strip()]
    metadata = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
    plan = build_layout_plan(blocks, metadata, text_width_mm=args.text_width_mm)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"groups": len(plan["groups"]), "warnings": plan["warnings"], "source_coverage": plan["source_coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
