# Inputs, Routes, and Semantic Aggregation

## Input identity

Both a PDF and a numbered PNG directory can serve as the source. `prepare_images.py --pdf` records the PDF file hash; `--pages-dir` records the identity of the complete numbered PNG set as "page number + file hash", independent of directory location. The new caller's `--source` accepts a PDF or that directory, and `--source-pdf` is kept as a compatibility alias. Renaming or moving an input does not change its content identity; adding, deleting, or changing pages changes the set identity.

Physical pages start at 1 and are separate from printed pages. An excerpt PDF may concatenate discontinuous source pages; do not merge automatically just because the physical pages are adjacent within the file; the printed pages and the original edition must be verified. When the source changes, reuse of old OCR/coordinates/in-book rules is forbidden.

Image preparation publishes `page-NNNN.json` atomically per page, and publishes `manifest.json` only after the complete request succeeds. Consumers can stream-read ready pages, but must still verify the full range at the end. Configure image size according to the current service's actual capabilities; the overview only locates, and body content is observed from native tiles/crops. The rendered page after rotation is the coordinate basis for page jobs; PDF point coordinates must be converted explicitly.

## Routes

First spot-check the text layer and the source images. When a usable text layer exists, use `native_text.py` to save native blocks/fonts/coordinates and a Markdown draft, and feed it into the same per-page proofreading queue; native extraction is not a correctness confirmation. Control characters, garbled text, abnormal formula order, and Type3 fonts are marked `needs_review`; do not run global NFKC or unknown replacements.

For scans, you can use visual OCR directly; when local resources are available, you can first try layout OCR and then have an independent vision model proofread. Currently `local_ocr.py` is only a MinerU transformers adapter (Qwen2VL, bfloat16/SDPA); it is not a unified entry point for all MinerU versions/devices. Other engines integrate by preserving native output, explicit type/coordinate mapping, and real samples. Do not assume SDK compatibility just because an interface has the same name.

Local OCR freezes all weight shards, index, processor/tokenizer/config, adapter, and runtime library versions. Missing index shards fail before the call. The GPU keeps one resident queue and does not load the model for every page. On shared devices, check the load first; do not change global shell, models, or shared environments.

## Modes and merging

`extract/proofread` do not need template admission; `typeset` needs real template evidence before production. Modes cannot upgrade content evidence. Page jobs output the same JSON, and only the coordinator that has performed source verification can confirm content/style or relationships.

`page_status=blank`, `blank_reason`, and `blocks=[]` express a blank page that has been viewed; the image viewing record remains complete. During assembly, generate a blank source record that contains no invented text and preserve page coverage; by default the reading version does not have to duplicate a blank sheet. A page with only a page number still has footer source content and must not pretend to have no elements.

Normalization preserves raw and the producer checkpoint. Type or text revisions use patches with before/after and the current source/image hashes; font inference uses independent rules and style-patches, and rule IDs execute no hidden action. When there is only a rule library and no explicit style patch, the original fonts do not change.

Aggregate chapters, lists, code, figures and tables, and footnotes in the true reading order; each body source block maps exactly once. Headers and footers are also kept in source-blocks; before discarding them from the reading layer, independently check bottom-of-page notes/footnotes. Heading candidates, paragraphs before and after a display formula, cross-page continuations, and caption attribution are covered in the [data contract](data-contract.md). The shared structure parser recognizes chapter numbers written as Chinese numerals/Arabic numerals or Chapter N; for unsupported numbering styles, use the source-reviewed `heading_presentation.depth/container_id` to specify the level and grouping explicitly, and do not rewrite the original text to force a match; when there is no evidence, keep unclassified.

Cross-page processing only extracts candidates automatically. A merge requires source-image re-check, consecutive pages, and an explicit in-book relationship; keep all source IDs, style offsets, and original text. The shared contract verifies preservation of formula, code, and list structure; list unknown capabilities explicitly, and do not downgrade to hide them.

## Export and acceptance

Text extraction can use `export_text.py` to export Markdown or retrieval chunks with provenance from the same JSONL. Use an explicit character budget for chunk size, and do not claim it is a tokenizer token count; never split a formula, table, or code block, and mark a single chunk that exceeds the budget as oversize. Raw semantic text always stays in the JSONL.

Typesetting uses `export_latex.py`; fonts, paper, type area, and figure size are read from the render_configuration of the source-bound profile. Production runs do not allow CLI parameters to silently override frozen configuration. Template changes only re-typeset and re-verify the affected parts, and do not re-OCR unchanged content.

Figure-as-code is mandatory only for editable reconstruction targets. In extract/proofread mode, or when the user explicitly chooses the source image, record the source-reviewed extraction_preserve/user_selected_original decision and keep it; there is no need to first fail a redraw. Photos/complex renders can be kept directly; report content fidelity and layout/editability separately.

Actually look at the final output and the original pages, and report separately the checked scope, unchecked content, text/formula uncertainties, structural loss, and layout problems. Passing structural, compile, or pixel-replay checks does not prove the whole document is accurate. For new samples and held-out evaluation see [Evaluation rules](evaluation.md); for dispatch, revision rounds, and stopping see [Parallel workflow](parallel-workflow.md).

Native text layer coordinates are unrotated PyMuPDF page coordinates; the page images already have page rotation applied. native_text.py saves rotation/cropbox/mediabox and pdf_to_prepared_matrix; apply that matrix first, then verify bbox against the source image, and do not only multiply by dpi. See [PyMuPDF coordinate and rotation specification](https://pymupdf.readthedocs.io/en/latest/page.html). A bbox revision with source evidence can be an explicit before/after patch to canonicalize, and the final step still verifies the source image dimensions and the valid range.
