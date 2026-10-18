# Textbook figure extraction, parallel reconstruction, and acceptance

This route runs in parallel with body-text OCR: first produce page images and lightweight layout localization, freeze the figure inventory, sizes, and style, then dispatch OCR and drawing jobs separately. Drawing can inspect the original page and adjacent pages independently to obtain data, without waiting for body-text OCR output; finally merge the two sets of results by stable figure_id. The currently reusable execution tooling is mainly Matplotlib; check backend choice and actual executable capability separately.

## Routing and provenance

- 2D charts with confirmable functions/data, simple geometric schematics, and 3D figures with confirmable projection relationships: reconstruct with Matplotlib, preferring genuine vector PDF. Save SVG/PNG previews in the workspace; the default LaTeX delivery takes only one selected PDF or PNG; export Markdown separately on demand.
- Structure diagrams, flowcharts, grammar diagrams, trees, timelines, window wireframes, and recoverable curves: inventory labels/relationships and then reconstruct as code; when numeric parameters are missing, consult the body text, caption, and adjacent pages first, and when the source schematic curve can be expressed faithfully, record the geometric anchors and their schematic nature. Native vectors can also be recovered from an auxiliary digital edition, but verify the scanned edition's labels, topology, and omitted branches item by item, record edition differences, and do not treat the digital edition as font ground truth.
- Retain the source image only for photographs, complex rendering, unrecoverable source detail, or concrete evidence of fidelity failure. Density, time cost, budget, inconvenience of one backend, or exhaustion of trial-drawing attempts does not prove that a figure cannot be reconstructed; never admit a screenshot on those grounds. If the figure cannot be completed, mark it needs_review, keep the working crop, and do not claim that image acceptance passed.
- Tables and ordinary formulas still prefer native text/math syntax; do not turn content that OCR could read into an image without cause. Complex figures that cannot be reliably redrawn are not forced into vector form; embedding a source image in a PDF is not pure vector.

Regions containing program text, formal grammar, derivation rules, or multi-line brackets must first be classified per [Code and special text](code-special-text.md): do not turn body text into a block figure merely because a brace sits next to it. Trees, state machines, block diagrams, and formula-dense structure diagrams can be evaluated for TikZ; geometry/timelines with reliable coordinates and simple mathematics can be evaluated for direct SVG; data curves and ordinary 3D prefer Matplotlib. Complex LaTeX formulas prefer native math; do not assume Mathtext supports arbitrary TeX. Dense labeling is not by itself evidence that a figure cannot be redrawn; admit a screenshot only when verification of other accurate methods still cannot recover the figure, and record the specific missing details, the evidence consulted, the methods evaluated, and their limitations; an actual failure must also retain the new render and a locatable fidelity defect.

Before extending a backend, first make a small sample from the same source with the same scene/size/labels/relationships: check the first draft, a real compile, PDF re-rendering, and bounded revision. Unify font, physical line width, arrowhead shape/length-to-width, dashes, and coordinate units; the verified template handles these, so each cheap model does not repeatedly guess the API. Preserve source spelling and uncertainty; do not change text inside a figure based on general language knowledge. A successful compile, a valid scene structure, and acceptance against the faithful source are distinct states. One model output only proves that this sample is feasible; it does not support ranking model or backend quality.

`figure_job.py`/`figure_render.py` and the existing packaging/replay entry points currently work around Python draw.py; a new TikZ/SVG backend must first connect independent builds, font/physical-canvas acceptance, and source-package replay before use in production. Final delivery still selects exactly one PDF or PNG per figure; the TikZ/SVG source code and actual dependencies must be packaged with the corresponding selected asset; an unsupported new backend must never masquerade as draw.py or leave only its output image.

Figure regions may come from an independent layout detector or visual localization; prior text recognition is not required. Use full-page overviews to check for missing figures, caption mismatches, cross-page figures, and multi-panel figures; ordinary formulas must not be mistaken for figures. Prefer one parent figure job for multiple panels, with sub-canvases fixed within the parent, so that separate subtasks do not scale independently. Record the figure-body bbox, caption bbox/text, panel relationships, source page/hash, and route; evaluate localization scores and reconstruction scores separately.

Generate a source-page image inventory independently of OCR, then reconcile it three ways with semantic figure blocks and admitted assets; even with every existing figure block bound, figures that OCR never output can still be missed. `audit_figures.py` can verify an explicit source inventory, including graphics such as arrows inside tables; when the cover is retained as a full page, represent it with a full-page asset, and do not reconstruct decorations one by one. If editions differ, check the table of contents/source images first; never fill in figures from an older book. Human source-page checking of the current inventory must not be passed off as the accuracy of fully automatic missing-figure detection.

Record page layout and drawing content separately. Preserve expressible source-supported relationships where possible: side-by-side figures in the same horizontal band, each with its own caption, or a complete paragraph arranged beside a figure. `figure_layout.py` reads explicit review decisions and checks bbox, source hash, caption and paragraph hashes/multi-page provenance; the export uses non-floating boxes so a figure does not drift into another chapter. Figure width always multiplies the whole-book type area, not a local column width; never shrink a figure a second time to make it fit side by side; if a column is too narrow, a paragraph is too long/has a footnote, or the actual height exceeds the page, change the plan to a complete sequence and re-export with a record; never truncate body text. For the specific data and the `--figure-layouts` entry point, see [LaTeX export](latex-export.md).

## Fix the whole-book scale first


Let the nominal width of the source page be `P` pixels, the unified page width be `C` mm, and the width and height of the figure canvas bbox be `w,h` pixels:

```
width_mm  = w / P * C
height_mm = h / P * C
figsize_inches = (width_mm / 25.4, height_mm / 25.4)
PNG_pixels ≈ figsize_inches * 400
```

Use the same whole-book scale in both directions, preserving aspect ratio; normalize different scan resolutions by each page's nominal width. Standardize bbox padding uniformly; text inside the figure is included in the canvas, and the caption is not drawn again. After the main coordinator freezes the canvas, submodels may change only the content layout; they must not change figsize, use bbox_inches='tight', or export every figure at the same width. A uniformly enlarged reading edition uses the export template's whole-book figure_scale and does not change each frozen canvas; update the whole-book C/profile and redraw only when the reconstruction scale itself changes; never enlarge individual figures by feel.

Padding added around the page by rotation correction does not count as paper width; save `nominal_page_width_px` and the expanded view dimensions separately. After perspective correction, paper width is determined by the target canvas from one review. If you use the type-area basis, first unify the type-area measurement method across all pages, and do not mix it with the full-page basis.

LaTeX maps uniformly by frozen source figure width / nominal page width × template's actual type area × whole-book figure_scale, uses `\includegraphics` with an explicit width and preserves aspect ratio; it does not stretch each figure to fill the type area. The AI-retrieval JSON retains millimeter dimensions and figure_id. Only when exporting Markdown separately, fix the PNG/SVG display width and measure the actual viewer; its basic image syntax has no universal width parameter, so use a few HTML images or uniform CSS when necessary, and do not claim that pure image syntax guarantees the ratio.



## One-time style calibration

Measure from representative pages: regular/math text style, font size, physical line width of curves/axes/guide lines, arrows, dash pitch, color, and background; save the actual font files/hashes, not only font names that may not exist. When the exact font name cannot be confirmed, choose a close unified substitute and note it. Chinese body glyphs and math glyphs may use separately configured fonts; a Chinese character unsupported in a math environment must not be silently replaced with an empty box.

`assets/figure_style.json` is only a neutral starting point; it must be reviewed against the book and then frozen. Style constraints unify default glyphs and line widths; they do not erase meaningful color, thickness, line style, or local emphasis from the source image; write differences into the reviewed `spec.style_override`. Within one book, drawing agents may not change fonts and color schemes on their own. Point/node labels first keep the original direction and short distance; if the actual render has no overlap, do not move them, and never move a label farther away preventively just because its bbox is near a line. After confirming overlap, first try different directions around the point at the same short distance, and adjust ha/va with the direction so the text box extends in that direction while the text itself stays upright; only when no direction at that distance has free space should you increase the distance in small steps. Use annotate's xy to anchor the point and offset points for xytext, which keeps the distance consistent. Record the overlap evidence, the direction/spacing attempts, and the final choice; other non-point labels also prefer nearby avoidance, and do not add a white background to every label. Only when there is actual overlap and nearby positions still have no suitable space, give that label a small opaque white, borderless background. Set `set_gid("label-background:stable-id")` on the child Text; the exporter's `label_background.mode=selective` processes only these markers, with a default padding of 0.04em; record the attempted positions and the reason for choosing a white background in scene/self-check. The white background is still a vector patch and must not cover key nodes, arrows, or intersections. A missing font or a missing glyph in a math label is a technical-stage failure; out-of-bounds/overlap still requires visual inspection.

The exporter's `pdf_fonttype=auto` uses Type42 by default for TrueType outlines and Type3 vector glyphs for CFF/OpenType outlines. In this Noto CJK measurement, the PNG showed the glyphs and the glyph coverage check passed, yet a PDF forced to Type42 could still lose Chinese characters. Therefore you must re-render and inspect the final PDF; never pass it based only on the absence of a missing-glyph warning or on the PDF containing no bitmaps. Type3 stays vector, but Unicode text extraction may be incomplete; save in-figure labels separately in scene.json for AI retrieval.

`assets/figure-template.py` lets submodels implement only `draw(fig, style, spec)`; `scripts/figure_render.py` handles export. The template rejects the unmodified demo figure by default, so that the demo parabola is not mistaken for a reconstruction result; set template_demo only in tool smoke tests. 2D can use lines/patches/text; 3D can use wireframe/surface; 3D in a PDF is a projected 2D drawing, not an interactive three-dimensional object. Rasterization of a complex surface must be recorded explicitly.

## Input images and on-demand context

`prepare_figures.py` generates a read-only shared `pages/` view and index. Drawing agents read the directory index and overviews first, then read native overlapping tiles of the pages containing the body text/formula on demand; the whole book's pages 1-N can be exposed, without attaching all images to the prompt at once. Functions, nodes, units, angles, and parameters often lie outside the figure; when they span pages, retain the pages actually used, image paths, regions/equation numbers, and source_formula/source_data/schematic_estimate evidence. A curve fitted only to appearance is not source-book data.


Submodels record the context they actually read; "can access pages" is not a substitute for having looked. Read permission covers only budgeted shared views and this job's inputs; original large pages are not placed in the readable shared directory; write permission is limited to this model/this figure/this attempt. An image read failure, a permission error, only a text file name, or a CLI attachment list is not enough to prove that the model received a clear image.

## Rotation, perspective, and nonlinear deformation

Keep the original page image and the original crop. First determine the full-page rotation/perspective from paper edges, body-text baselines, or trusted page geometry, then apply the same page-level transform to the image and OCR branches, and record the forward and inverse coordinate mappings; never infer page rotation from slanted axes, slanted curves, or 3D projections inside a figure.

The current preparation tool implements reviewed page_quad perspective homography and deskew angles; unreviewed automatic deformation is prohibited. `--page-transforms` example:

```json
{"38":{"reviewed":true,"deskew_degrees":0.3,"evidence":"manual check of body-text baselines"}}
```

Perspective items include page_quad_px (TL/TR/BR/BL), rectified_size_px, and reviewed. Nonlinear deformation such as spine curvature must not be passed off as fixed by a homography matrix; use a separate grid dewarping tool based on paper surface/body-text baselines, save the remap grid and mapping, verify against the original page, and only then proceed to extraction. The current tool does not implement nonlinear dewarping; when correction cannot be done reliably, retain the source image and needs-review state rather than distorting curves to pursue visual similarity.

## Parallel queues, scripts, and directories

Freeze size/style first, then dispatch jobs to different models. Prefer one short context per figure; during trials, use CLI concurrency 2-4, and rate-limit the OCR GPU queue and the cloud drawing queue separately; production concurrency depends on API rate limits, local resources, and budget, and subagents must not be stacked without bound. Data-context localization and drawing can also be done within the same figure job; for complex figures, produce scene/data first and then write the script.

```
figures-work/
  inventory.json                    figure_id/source page/bbox/size/route
  source/pages/0001/                 original pages and corrected views; models do not read large pages directly
  shared/style.json                  frozen style
  shared/pages.json                  image size/coordinates/transform index
  shared/pages/0001/                 overview + budgeted native tiles
  figures/p0038-fig02-04/
    spec.json                        frozen canvas, provenance, style version
    reference/source-original.png    original crop
    reference/source.png             corrected comparison crop
    input/                           budgeted view of the figure body
  jobs/p0038-fig02-04/qwen-flash/attempt-01/
    input/                           spec/style/template/context index snapshot
    output/draw.py                   reusable drawing script that must be retained
    output/scene.json                structure/labels/data provenance/uncertainties
    output/data.json                 optional data file
    render/                          PDF/SVG/400dpi PNG + export metadata
    review/                          same-scale side-by-side/overlay + technical results
    output/self-check.json           post-render self-check, position attempts/white-background rationale
    feedback.json                    specific revision comments for this round
    run.json                         CLI model/usage/duration/read record
  jobs/.../attempt-02/               new input snapshot and revisions; old versions are not overwritten
  final/p0038-fig02-04/              only versions that passed acceptance or explicitly fell back to the source image
  final/figures.json                 admitted version/route/size/evidence/link
```

Retain all figure scripts, data, scenes, and revision comments; only the coordinator changes the version of a shared profile. Different AIs must not overwrite files with the same name. For final release, the main coordinator selects versions once; body text is merged using figure_id placeholders, and captions/cross-references are verified against OCR results. When a figure has not passed, a working draft may temporarily merge the source image to keep text production moving, but it must be explicitly marked needs_review; final admission of a screenshot must still meet the fallback threshold, and a working-draft placeholder must not be counted as image acceptance.


When verifying geometric points, distinguish the position of the point marker from that of its text label; a label in the source image may have been moved to avoid overlap, so do not treat the text bbox as the point coordinate. Before moving a point, compare the body text's intersection/projection/iteration relationships and the actual point markers; if text and figure contradict, record the doubt, and do not correct based only on the text position in the figure. When a review retracts a false positive, keep the source evidence; never treat the coordinator's initial review opinion as authority above the source book.

## Acceptance and limited revision

1. **Technical check**: the script can be rerun; size is correct; 400dpi (reconstructed raster), font coverage, and input/output hashes match; call a PDF pure vector only when its MediaBox is correct and it actually contains no image XObject; for SVG, check image/feImage. After re-rendering the final PDF, check for actual missing glyphs; cropping/overlap also needs visual verification, and the current technical scripts do not judge these semantic issues automatically. A source-image fallback retains native pixels and the actual effective DPI; never upsample to fake high quality.
2. **Hard content conditions**: all readable labels, sub/superscripts, units, ticks, axis scales, counts of points/curves/arrows, and solid/dashed line identity and direction are correct; schematic connections/containment/intersections/relative positions and 3D projection/occlusion match the source image. Body-text data provenance is traceable; unknown values must not be filled in by guessing. Distinguish strict source-figure replication from a parsing study edition accepted by the user: function plots with body-text data are drawn from the true analytic expression, local shape differences caused by hand-drawn dashed segmentation need not be fitted deliberately, and a figure must not fall back solely because of them. A suspected tick error in the source image must be handled based on context and user authorization, and recorded in source_deviations; never change it covertly or claim it is identical to the source image.
3. **Visual comparison**: render the candidate at the same physical scale as the source image; inspect the side-by-side view and the edge overlay separately; compare the proportion within the canvas, key anchors, ratios, whitespace, label positions, font size/line width. Cleaning scan noise can increase pixel differences; SSIM/ink/edge distance are only for locating problems, and a threshold alone must not grant automatic acceptance. Never apply arbitrary nonlinear registration to a candidate to boost similarity.
4. **Drawing AI self-check, then independent acceptance**: after the main coordinator runs the script, return the actual PNG and a budgeted preview of the re-rendered final PDF to the drawing AI. The drawing AI must check item by item: labels/sub-superscripts, position/occlusion (especially curves near ticks), axis ticks, solid/dashed lines, leader endpoints, nodes, cropping, fonts/line width, and body-text data; fix obvious problems first; write self-check.json recording the images/hashes viewed, problems and actions, occlusion evidence/fixed-distance direction attempts/distance increments for node labels, white-background rationale, and source_deviations. Without viewing a real preview, the result can only be called a script-level check. After modification, re-render and look again; do not accept an unreviewed new script directly. The drafter's self-assessment does not count as final acceptance; the main coordinator or an independent visual reviewer gives item-by-item pass/fail/uncertain with source-image locations and returns specific actionable comments. The reviewing model can also be wrong, so keep uncertainties. A potentially useful geometric starting value is a key-anchor error of about 2% of figure width, but calibrate it on representative figures from this book first; it is not an exemption for semantic errors.

Acceptance states are prepared/produced/technical_pass/needs_revision/accepted/retained_source/needs_review; do not describe a technical pass as "close enough". The acceptance record includes the accepted attempt, scene and source crop hashes, technical report, content conditions, and visual review comments; a hard content condition with fail/uncertain must not be treated as a faithful reconstruction that passed.

By default, allow at most two targeted revision rounds after the first draft (including the post-render self-check revision), and if necessary one model switch; budget or user instruction can override. Before switching, check the prompt path, tool image reading, context scope, image downsampling, and the data and nodes in the scene. Two strong models failing in the same way may also indicate insufficient source information or unsuitability for reconstruction; do not attribute it directly to the prompt. If the figure still fails, admit retained_source only when specific source detail or actual reconstruction-failure evidence meets the fallback threshold below and source review has passed; exhausting attempts or budget is not itself grounds for fallback. Otherwise mark needs_review, keep the working crop, and do not claim deliverable acceptance.

## Model selection and providers

The model is specified by the current job or inherits the user's OpenCode default; it is not restricted by a vendor/model allowlist. catalog_figure_models filters by actual image input, text output, tools capability, and explicit budget; --family is only an optional filter; record the date and the actual service route. Verify the default model with a small real-image test; never assume capability from its name. The Codex backend explicitly provides the currently available models. For OpenCode version compatibility and default invocation, see [Invocation reference](opencode.md).

## Tool invocation example

First prepare the in-book region inventory (native source pixels), then review the style:

```bash
python scripts/prepare_figures.py --pdf /absolute/book.pdf \
  --regions /absolute/regions.json --style /absolute/book-style.json \
  --output /absolute/figures-work --page-width-mm 170 \
  --context-pages 1-20 --max-pixels 1500000 --max-edge 2000
python scripts/catalog_figure_models.py --output /absolute/catalog-YYYYMMDD.json \
  --max-input-price 1 --max-output-price 5
python scripts/figure_job.py --prepared-root /absolute/figures-work \
  --spec /absolute/figures-work/figures/FIGURE_ID/spec.json \
  --style /absolute/figures-work/shared/style.json \
  --context-index /absolute/figures-work/shared/pages.json \
  --job-dir /absolute/figures-work/jobs/FIGURE_ID/MODEL/attempt-01 \
  --backend opencode
```

By default the tool only prepares; when the current job is authorized, add `--execute` to make an actual call with a new job directory; there is no unlimited automatic retry. List the current MCPs and then `--disable-mcp` each one. For a revision job, add `--previous` with the previous round's output and `--feedback` with specific comments, and use a new attempt; use absolute input/output paths for models and templates so nested job directories are not misparsed. The agent writes only the script; the main coordinator inspects it and then runs it:

```bash
python JOB/output/draw.py --spec JOB/input/spec.json --style JOB/input/style.json \
  --renderer JOB/input/figure_render.py --output JOB/render
python scripts/figure_review.py --spec JOB/input/spec.json \
  --render-meta JOB/render/render-metadata.json \
  --source-crop FIGURE/reference/source.png --output JOB/review
python scripts/retain_figure.py --spec FIGURE/spec.json \
  --source-crop FIGURE/reference/source.png \
  --original-crop FIGURE/reference/source-original.png \
  --output NEW_FALLBACK_DIR --reason "reconstruction acceptance failed"
```

After the actual render, dispatch a self-check/revision round: figure_job uses a new job directory and adds `--previous JOB/output --feedback /absolute/check-request.json --render-preview /absolute/budgeted-actual-render.png`; render-preview can be repeated to supply PDF renders or tiles. The tool verifies the actual PNG dimensions, copies it into this round's input, and attaches it to the model, requiring self-check.json output; for an over-budget preview, produce an overview/tile first and do not upload the large image directly. The first-draft round still does not grant the sub-AI permission to execute scripts; the coordinator runs it and returns the preview, which completes the self-check loop.

Data evidence in scene.json enters the render spec or the final acceptance record only after actual confirmation; a model's self-reported provenance does not automatically become source_checked. The technical review still outputs semantic_accuracy=not_evaluated. By default the script retains all outputs and refuses to overwrite; PNG previews and PDFs are both generated from the same frozen canvas.

Image preparation requires Pillow/PyMuPDF/numpy; page-level correction additionally requires OpenCV; drawing requires Matplotlib/fontTools. Do not install a different dependency set for each sub-AI; unify the environment and record versions. Account for time/cost separately for drawing, acceptance, revision, and source-image retention; count repeated image/context cache hits, cancellations, and failed calls too; for OpenRouter, prefer the generation's actual cost; a CLI catalogue cost must not be passed off as a verified charge.

## Deliver code together with LaTeX

The scripts, spec/data/scene, and shared renderer and style of finally admitted figures are delivered with the source package; a source-image fallback carries only the selected asset. After release, freeze the code hash so that later edits to a multi-figure shared script do not separate the code from the final images. For the directory structure inside the package, font path migration, and verification of an actual redraw, see [Source packaging](source-package.md).

## Executable threshold for screenshot fallback

`retain_figure.py --assessment decision.json`, production export, and packaging all call `figure_fallback.validate_fallback`. Each `retained_source` figure records `fallback_assessment={status:"source_checked",category,reviewer,reason,evidence:{path,sha256},asset_sha256}`. category allows only photograph/complex_rendering/unrecoverable_source/reconstruction_failed; the latter two also require a non-empty missing_details and alternatives=[{method,limitation}], and an actual failure additionally requires attempts=[{path,sha256,defect}]. Keep evidence paths in the workspace; the source package retains the decision summary. A hash proves identity, not that the reason is true; the main reviewer must still look at the source image and the final PDF. An old registry missing an assessment is refused for production export; fill in each check individually, and never fabricate reasons in bulk.

The independent figure inventory checks more than coverage: the final review also lists the fallback basis for every source-image screenshot and checks whether every figure expressible as code has been vectorized. Retaining a scanned cover as a full page does not fall under this threshold. Placing a raw bitmap in a PDF, automatically tracing pixel outlines, or changing the file extension does not count as structural reconstruction. A vector recovered from an auxiliary digital edition must be explicitly marked native_vector_recovery; it must not be reported as a new drawing, and the auxiliary edition's whole book must not be packed into the source package.

A PDF produced from an SVG/native vector recovery must undergo separate acceptance of the actual conversion, checking at least dashes, clipPath/masking, pattern, text outlines, and final physical size; never assume that any SVG parser fully supports these features. Different SVG converters may lose dash/clip/pattern, so the chosen backend must be verified on source/product; when unitless SVG coordinates exported from a PDF are in pt, set dpi=72 explicitly, otherwise the default 96 shrinks them to 75%. Determine the specific backend and parameters from this SVG's units and the actual render. A pattern in a native PDF may also embed a JPEG texture; a slanted line expressible as code should be changed to an explicit vector line with clipping. `get_images()` lists only image XObjects and cannot prove there are no inline/pattern pixels; also check `get_image_info()`, SVG image/feImage, and the actual render. An image bbox outside the canvas may still be referenced by a pattern, so it must not be discarded as irrelevant decoration on that basis.

Extraction/proofreading mode can use fallback_assessment.category=extraction_preserve with workflow_mode=extract|proofread; when the user explicitly chooses the source image, use user_selected_original with user_requested=true. Still retain source evidence, reason, and asset hash, but there is no need to attempt a redraw first. An editable reconstruction target in typeset follows the specific feasibility/failure-evidence rules.
