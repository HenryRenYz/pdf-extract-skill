# Packaging LaTeX source, images, and drawing code

Packaging happens after final image and PDF acceptance; it does not re-recognize body text and does not compress the entire working directory for the user. The delivery scope is determined by the current user's choice.

## Package structure

```text
dist/
  main.tex
  .latexmkrc                      selects XeLaTeX for latexmk
  BUILD.txt                       entry point, build commands and font requirements
  book-config.tex
  book-style.sty
  chapters/*.tex
  images/                         the single selected PDF or PNG per figure
  covers/                         whole-page covers used by this book
  figure-code/
    rebuild.py                    unified redraw entry point
    renderer.py                   shared renderer matching the final figures
    requirements.txt              dependency versions of the environment used to verify redraws
    requirements-mpl-<version>.txt required historical runtime version, carried when present
    manifest.json                 figure number → image → code/data/style and hashes
    README.md                     how to run and modify figures
    shared/styles/<hash>/         frozen styles and their relative-path fonts
    shared/fonts/                 fonts referenced separately by scripts
    shared/licenses/              license files kept with fonts
    figures/<figure_id>/
      draw.py                     the single selected final script
      spec.json                   actual canvas, coordinates, and style override parameters
      data.json                   numeric/function/geometric data, included when present
      scene.json                  scene and source description, included when present
```

Deliver the compiled `main.pdf` separately; by default the source ZIP does not also contain the whole-book PDF. Do not include candidate figures, multiple format copies, old script attempts, acceptance screenshots, model sessions, OCR page images, all source sidecars, build logs, or aux/toc/out files. The spec/data/scene/manifest JSON needed for redrawing are delivery dependencies and are not deleted for "JSON cleanup".

## Compiler and body-text fonts

The exporter writes `% !TeX program = xelatex` and `% !TeX encoding = UTF-8` at the start of `main.tex`, including when a custom template had conflicting program/encoding hints. The default template additionally checks `RequireXeTeX` before loading ctex, so choosing pdfLaTeX produces a direct engine-requirement error. Magic comments are editor hints, not TeX instructions; a cloud platform may still need XeLaTeX selected in its settings. The delivered `.latexmkrc` selects XeLaTeX, and `BUILD.txt` supplies explicit build commands and explains auxiliary-file convergence.

Body-text fonts are **external dependencies**, independently of the portable drawing-font bundle below. The default style refers to configured Noto family names and conditionally to KaiTi for explicit Kai spans; these names are not guaranteed to be installed on Windows/macOS/Linux or a cloud service. The exporter records configured roles and which optional Kai/code fonts are used in the sidecar's `font_dependencies`, and lists them in `BUILD.txt`. This is not a complete font audit of a custom template with additional hard-coded declarations. Body fonts are omitted by default, and PDF-embedded font subsets do not make the source rebuildable.

Before source packaging, ask once: "Include body-text font files with the source ZIP?" Offer **do not include (default)** and **include font files and licenses**. Reuse the user's explicit choice for the current book. With no answer to this optional question, proceed with the default; a general request to package the book is not itself an opt-in to including fonts. Do not ask for this choice during extract/proofread or when no source ZIP is being delivered.

Default packaging needs no extra flags. If the user explicitly opts in, select the actual required regular/italic/bold faces and collection files, verify that their accompanying licenses permit the intended inclusion, then use:

```bash
python scripts/package_latex.py --dist /path/to/dist \
  --export-manifest /path/to/dist-export.json --zip /path/to/source.zip \
  --include-body-fonts \
  --body-font /path/to/selected-font.otf \
  --body-font-license /path/to/accompanying-license.txt
```

Repeat the file/license flags as needed. The packager checks font-file signatures and includes `body-fonts/` files, licenses, a hash manifest, and installation instructions **in the ZIP only**; it does not modify accepted TeX, PDFs or system fonts. It updates the ZIP's BUILD.txt when available, and records the inclusion separately in the export sidecar. The source still uses configured family names: the recipient must install the supplied fonts or explicitly configure relative file loading. File inclusion alone does not establish complete font coverage or automatic font discovery. Drawing-font dependencies remain governed by their separate drawing bundle.

For a fully relocatable body-font package, use redistributable fonts with their licenses and load them through package-relative files, or validate a book-specific template using font files supplied by the target TeX distribution (for example, Fandol). Replacing font families changes glyph metrics, wrapping and pagination, so it requires visual acceptance and new template admission; do not silently replace fonts during packaging or label a local build as cross-platform validation.

Figures, QR codes, and covers that retain the source image carry only the final asset, are marked `retained_original`, and do not get fabricated drawing code. Providing only the drawing script while omitting the shared renderer, the real spec, style, or data does not count as a reusable source package.

## Freezing and correspondence

When an image is published, save the selected script, spec, style, renderer, data, and font hashes, and record explicit paths in the registry; `script_path/spec_path/style_path/render_metadata_path` is recommended. After rendering, do not overwrite an accepted script, even if the file contains branches for multiple figures. If a change is required, redraw from a new snapshot and then accept it; do not continue using an old render record to prove new code.

`package_latex.py` reconciles the images actually used by the LaTeX with the selected-figure registry and checks the image, spec/style, and renderer hashes. When an old registry lacks paths, you can supply an explicit `--drawing-inputs`, each item containing `id/script/spec/style/metadata`; do not guess another candidate script from the figure number. If the script hash differs from the old render record, you must verify by actually redrawing; accept only after an actual replay comparison, and do not fabricate the original hash.

Drawing programs use relative paths inside the package and do not depend on the current user's home directory. The tool only relocates font files and keeps the drawing geometry and data unchanged; it records the original-script and packaged-script hashes and a migration list. Carry required fonts together with their licenses and identify fonts by file hash, to avoid writing only a family name and landing on another font. Source-image paths inside source JSON are provenance records only; redrawing does not require them to exist.

## Packaging and redrawing

After `export_latex.py` has completed and been accepted:

```bash
python scripts/package_latex.py \
  --dist /absolute/work/latex/dist \
  --export-manifest /absolute/work/latex/export-manifest.json \
  --zip /absolute/work/latex/book-latex-source.zip \
  --drawing-inputs /absolute/work/drawing-inputs.json \
  --python /absolute/drawing-environment/bin/python \
  --verify-drawings --verify-output /absolute/fresh/replay \
  --font-license /absolute/font-license.txt
```

Omit `--drawing-inputs` when the current registry has complete paths. Use the environment used for the final drawings, and record dependency versions as requirements; if dependencies are missing, set them up in an isolated environment first and do not change the shared global environment. When repackaging an existing `figure-code`, use `--force` after review; redraw output needs a new directory outside the package. `--font-license` can be specified repeatedly.

Source images may have been generated by several Matplotlib versions. Do not promise pixel-by-pixel reproduction merely because the code is the same: cross-version redraws can differ in rendering. The manifest records the version for each figure, and the requirements-mpl file keeps only the needed dependency configuration, without stuffing the entire conda environment into the package. During package verification, specify the corresponding environment with repeated `--runtime VERSION=/path/to/python`; the unified entry point dispatches, so you do not start each figure manually. Retries can use `--reuse-replays`, but the tool must re-check the script/spec/style/renderer/version and PNG hashes, then compare pixels; do not reuse based only on an old "passed" message.

The user unpacks the source package and runs the following in the package root:

```bash
python figure-code/rebuild.py --output /absolute/fresh/rebuilt --formats pdf
python figure-code/rebuild.py --figure FIGURE_ID --output /absolute/fresh/one --formats png
python figure-code/rebuild.py --output /absolute/fresh/exact --exact-runtime --runtime VERSION=/absolute/matching/bin/python
```

By default only vector PDFs are redrawn, and PNGs are generated at the frozen 400dpi; items that retain the source image are skipped. Output does not overwrite accepted images; inspect the new figures and confirm them before replacing the corresponding files. LaTeX compilation uses images directly and does not depend on installing the drawing environment or running drawing scripts temporarily.

Without exact-runtime, you can redraw everything uniformly in the current environment and explicitly state the original version differences; this only makes modification easier, and you must not pass off results from a different software environment as a pixel-by-pixel replay of the source image.

## Delivery checks

- Each actually referenced image appears exactly once, and each redrawn figure has its corresponding selected code and dependencies; source-image fallbacks are explicitly marked.
- Actually run the redraw entry point from the packaged relative paths, and check output against accepted figures for size, labels, and pixel/visual results. A syntax check alone does not prove the script runs; when differences come from a different software environment, record them and look at the figures again, and do not claim cross-platform pixel identity.
- Verify ZIP integrity, the file list, and in-package hashes, and confirm there is no dependency on a working directory outside the package, no missing data, no old candidates, and no compilation junk.
- If only drawing source is added, the hashes of the whole-book PDF and the previously accepted images should stay the same. Update the source package, the export sidecar, and the file count and ZIP hash in the delivery manifest; historical acceptance records must not stand in as proof for the new package.

An excerpt with no figures also uses package_latex.py: when export-manifest.figures is empty, --figures is not required, and no empty figure-code or Matplotlib dependency is generated. The special-text UTF-8 original of code/grammar, rendered assets, and the necessary manifest are still actual compilation dependencies and are delivered with the clean source; main.pdf is provided separately.

## Backend build recipes

A non-Matplotlib asset can provide `build_recipe={backend:svg|tikz|native_pdf,source:{path,sha256},dependencies:{in-package relative path:{path,sha256}}}` in the registry entry. A TikZ source is a complete independently compilable tex; SVG keeps its real units and does not apply some book's dpi; native_pdf stores the recovered native vector source and must not be called re-derived geometry. Dependencies must not escape the package directory. The unified rebuild.py supports these backends, and --verify-drawings compares the originally accepted figures with the actual rebuilt PDF page by page; the corresponding CairoSVG or XeLaTeX environment is required. When a backend is not yet integrated, reject it explicitly and do not fabricate Matplotlib records.

Before packaging, the existing file hashes in export-manifest are checked; after source or asset changes you must re-export/update acceptance, and must not merely regenerate the ZIP manifest to conceal the changes. A package containing only source images does not require a Matplotlib runtime environment.
