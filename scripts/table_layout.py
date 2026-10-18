#!/usr/bin/env python3
"""Validate source-reviewed table ruling and generate native tabular fragments.

The exporter owns cell contents and grid construction. This module owns only
source line topology, never inferring a grid or booktabs styling from text.
Segments use zero-based boundaries and half-open cell intervals. For example
horizontal {boundary: 1, start: 0, end: 4} is the line below a four-column header.
"""
from __future__ import annotations

import math
from copy import deepcopy


class TableLayoutError(ValueError):
    """The source ruling is unavailable, malformed, or conflicts with cells."""


def _integer(value, name):
    if type(value) is not int:
        raise TableLayoutError(f"{name} must be an integer")
    return value


def validate_table_layout(layout, grid, row_count, column_count):
    """Return validated layout; grid maps (r,c) to (origin_r,origin_c,cell).

    Require source_checked evidence even for an explicitly borderless table.
    Any line through one merged cell is rejected rather than silently erased.
    Missing metadata should route to source review or retained-image fallback.
    """
    if not isinstance(layout, dict):
        raise TableLayoutError("table_layout is missing: review source borders or retain the source table image")
    if type(layout.get("schema_version")) is not int or layout.get("schema_version") != 1 or layout.get("evidence") != "source_checked":
        raise TableLayoutError("table_layout must be schema 1 with source_checked evidence")
    if row_count < 1 or column_count < 1:
        raise TableLayoutError("empty table grid")
    if (type(layout.get("row_count")) is not int or type(layout.get("column_count")) is not int or
            layout.get("row_count") != row_count or layout.get("column_count") != column_count):
        raise TableLayoutError("table_layout dimensions do not match parsed cell grid")
    for field in ("source_image_sha256", "text_sha256"):
        value = layout.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise TableLayoutError(f"table_layout requires a verified {field}")
    normalized = deepcopy(layout)
    for axis, boundary_limit, interval_limit in (
        ("horizontal", row_count, column_count),
        ("vertical", column_count, row_count),
    ):
        segments = normalized.get(axis)
        if not isinstance(segments, list):
            raise TableLayoutError(f"table_layout.{axis} must be an explicit list (empty for no rules)")
        occupied = set()
        for segment in segments:
            if not isinstance(segment, dict):
                raise TableLayoutError(f"invalid {axis} segment")
            boundary = _integer(segment.get("boundary"), "boundary")
            start = _integer(segment.get("start"), "start")
            end = _integer(segment.get("end"), "end")
            if not 0 <= boundary <= boundary_limit or not 0 <= start < end <= interval_limit:
                raise TableLayoutError(f"{axis} segment is outside table grid")
            style = segment.setdefault("style", "single")
            if style not in ("single", "double"):
                raise TableLayoutError("only single/double source rules are supported; retain complex source rules")
            weight = segment.setdefault("width_pt", 0.35)
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or not 0.1 <= weight <= 2:
                raise TableLayoutError("source rule width_pt must be finite and between 0.1 and 2")
            # Native tabular vertical rules share arrayrulewidth. Different
            # vertical weights need a dedicated renderer, never a quiet approximation.
            if axis == "vertical" and abs(weight - 0.35) > 1e-9:
                raise TableLayoutError("native vertical rules require width_pt=0.35; retain other source weights")
            for index in range(start, end):
                key = (boundary, index)
                if key in occupied:
                    raise TableLayoutError(f"overlapping {axis} rule segments")
                occupied.add(key)
                if axis == "horizontal" and 0 < boundary < row_count:
                    a, b = grid.get((boundary - 1, index)), grid.get((boundary, index))
                elif axis == "vertical" and 0 < boundary < column_count:
                    a, b = grid.get((index, boundary - 1)), grid.get((index, boundary))
                else:
                    continue
                if a is not None and b is not None and a[:2] == b[:2]:
                    raise TableLayoutError(f"{axis} rule crosses merged cell at boundary {boundary}, index {index}")
        segments.sort(key=lambda x: (x["boundary"], x["start"]))
    return normalized


def horizontal_rules(layout, boundary):
    """Return TeX commands for one row boundary, including local rule weight."""
    commands = []
    width = layout["column_count"]
    for segment in layout["horizontal"]:
        if segment["boundary"] != boundary:
            continue
        weight = segment.get("width_pt", 0.35)
        if weight != 0.35:
            commands.append(r"\noalign{\global\arrayrulewidth=" + f"{weight:g}pt" + "}")
        rule = (r"\hline" if segment["start"] == 0 and segment["end"] == width else
                r"\cline{" + f"{segment['start'] + 1}-{segment['end']}" + "}")
        commands.extend([rule] * (2 if segment.get("style", "single") == "double" else 1))
        if weight != 0.35:
            commands.append(r"\noalign{\global\arrayrulewidth=0.35pt}")
    return commands


def _vertical_style(layout, row, boundary):
    for segment in layout["vertical"]:
        if segment["boundary"] == boundary and segment["start"] <= row < segment["end"]:
            return "||" if segment.get("style", "single") == "double" else "|"
    return ""


def cell_spec(layout, row, column, colspan=1, align="c"):
    """Return the source-specific column spec, including rowspan placeholders.

    The exporter puts stable full-height boundaries in the native preamble.
    Only spans or row-specific differing boundaries use this multicolumn spec.
    Internal boundaries are owned by the preceding cell, so a source single
    line does not accidentally become a double line. Outer left is owned by
    the first cell. For mixed/partial vertical rules, row-specific specs apply.
    """
    if (any(type(value) is not int for value in (row, column, colspan)) or
            not 0 <= row < layout["row_count"] or align not in ("c", "l", "r") or
            colspan < 1 or column < 0 or column + colspan > layout["column_count"]):
        raise TableLayoutError("invalid cell spec request")
    left = _vertical_style(layout, row, 0) if column == 0 else ""
    right = _vertical_style(layout, row, column + colspan)
    return left + align + right
