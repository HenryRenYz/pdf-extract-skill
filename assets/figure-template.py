#!/usr/bin/env python3
"""Copy this file into a figure job; replace draw() and retain the export contract.

Usage: python figure-template.py --spec spec.json --style figure_style.json
       --output output [--renderer /path/to/scripts/figure_render.py]

The explicit renderer path supports job folders outside the installed skill.
The coordinator freezes canvas dimensions and style before parallel dispatch.
Use normalized figure coordinates / explicit margins, not bbox_inches='tight'.
"""
import argparse
import importlib.util
from pathlib import Path


def draw(fig, style, spec):
    """Replace this demonstration; never change fig size or export files here."""
    import numpy as np

    # Optional shared contract:
    # spec.scene: labels, axes, geometry and source layout observations.
    # spec.data: formulas/nodes/parameters, plus provenance entries containing
    # physical page, formula identifier, bbox_px and source-image identity.
    # spec.context: shared_page_dir and selected_pages for the worker to inspect.
    # spec.uncertainties: unresolved facts; do not invent missing node positions
    # or "correct" a schematic into a conventional idealized mathematical plot.
    # A shared scene.json may feed several workers, but its ownership is held by
    # the coordinator; each worker exports only to its own revision directory.
    if not spec.get("template_demo", False):
        raise NotImplementedError("Implement the source-specific draw() callback; template_demo is for exporter smoke tests only")
    ax = fig.add_axes([0.16, 0.18, 0.79, 0.75])
    x = np.linspace(-2, 2, 201)
    ax.plot(x, x * x, color=style["color"], linewidth=style["line_width_pt"])
    ax.set(xlim=(-2, 2), ylim=(0, 4.2), xlabel="$x$", ylabel="$f(x)$")
    ax.spines[["top", "right"]].set_visible(False)
    # Keep marks, arrows and labels inside the frozen canvas. Record source
    # uncertainty in the job review data; do not invent points or parameters.
    # Keep point labels close. Only on actual overlap, try other directions
    # around the point at the SAME gap before increasing that gap. Keep the
    # glyph upright; annotate(xy=point, xytext=offset, textcoords='offset points')
    # preserves the anchor relation. Leave already clear labels in place.
    # Verify at final LaTeX embed scale (spec.context when supplied), not just
    # a large preview. Function labels use display-style mathtext fractions,
    # e.g. r'$\dfrac{1}{1+x^2}$', to keep numerator/denominator readable.
    # The exporter does NOT back every text item.
    # Only unavoidable overlaps: label.set_gid('label-background:STABLE_ID').
    # Record tried positions and justification in scene.json/self-check.json,
    # and verify the small backing does not hide nodes or arrowheads.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--style", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--renderer", type=Path)
    parser.add_argument("--formats", nargs="+", choices=["png", "pdf", "svg"], default=["png", "pdf", "svg"])
    args = parser.parse_args()
    renderer_path = args.renderer or Path(__file__).resolve().parents[1] / "scripts" / "figure_render.py"
    if not renderer_path.is_file():
        parser.error("Exporter not found; supply --renderer pointing to the installed skill's scripts/figure_render.py")
    module_spec = importlib.util.spec_from_file_location("figure_render", renderer_path)
    renderer = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(renderer)
    metadata = renderer.render(draw, args.spec, args.style, args.output, __file__, args.formats)
    print(f"{metadata['id']}: {', '.join(metadata['files'])}; {metadata['vector_kind']}; review required")


if __name__ == "__main__":
    main()
