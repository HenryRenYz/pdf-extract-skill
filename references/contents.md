# Automatic table of contents: generation, numbering, and acceptance

Read this file when maintaining the table of contents. Template configuration is in `assets/latex/book-style.sty`, entry selection is in `scripts/latex_contents.py`, and export and compilation are in `scripts/export_latex.py`. Reuse the accepted body text; do not re-OCR the table of contents or the whole book.

## Generate from body text

The reading edition outputs only the standard `\tableofcontents`. The original book's table of contents is used to verify inclusion scope, order, and appearance; the original transcription and printed page numbers remain in the semantic data and provenance sidecar, and are not copied into another hand-written table of contents.

Source table-of-contents parsing supports dot leaders or ellipsis leaders, tab/multiple-space column separation, and Arabic/Roman page numbers with or without parentheses. The same table of contents may mix these. Except for the table-of-contents title and blank lines, unparsed lines raise an explicit error; first source-review and add parsing capability or an explicit mapping; do not silently drop lines. Entries preserve the original line and parsing statistics.

| Source level | Body command | TOC level |
| --- | --- | --- |
| Chapter, depth 1 | `chapter` | 0 |
| Section, depth 2 | `section` | 1 |
| Subsection, depth 3 | `subsection` | 2 |

A three-level table of contents sets `tocdepth=2`; `secnumdepth` controls body numbering, and the two must not be mixed. The table-of-contents title and chapter entries inside the table of contents are not new body chapters.

Numbered headings are produced by native counters, with a stable `label` immediately after the heading. Use the standard short-title argument `[TOC title]` to separate body emphasis from the table-of-contents font; do not let body `textbf` override the global table-of-contents font, and do not repeat the chapter number in the short title. ctex already writes `第N章` (Chapter N) into `numberline`; wrapping `第`/`章` again duplicates it and pushes the number out of its box.

Unnumbered headings such as "Commentary", "Review and Discussion Questions", and "Exercises" that the source table of contents actually includes are added at their body-text position with the standard `addcontentsline`. Preserve optional-reading asterisks; do not omit them just because the heading uses a starred command; exceptions to printed numbers require a provenance mapping, and you must not hand-write the entire table of contents based on them. Headings inside algorithms, chapter names in answers, and index letters are not added automatically. Bind each entry to the real body-text heading; report duplicate, ambiguous, or out-of-range entries explicitly, and do not construct targets that do not exist.

For math headings, use hyperref's `texorpdfstring` to provide a safe bookmark. A real footnote on a heading remains a native body-text footnote; short titles/table of contents/bookmarks do not use the footnote body, to prevent duplication or loss caused by moving arguments.

## Global formatting and page-number column

Use mature `titletoc` to configure per-level fonts, indentation, chapter spacing, hanging continuation lines, dot leaders, and parenthesized page numbers; do not patch spaces, dots, or negative spacing entry by entry. Calibrate specific font sizes against the source book and reading requirements.

Page numbers and filler dot leaders are independent of entry font size and use a uniform font, size, and weight; chapter/section/subsection headings may use different font sizes. The page-number box uses a fixed physical width verified for the current document, avoiding alignment shifts caused by an `em` that changes with different level font sizes.

Measure all page-number fonts and the right-edge coordinates on the same page. Increasing the number of digits changes the left edge while the right edge should stay consistent; keep a reasonable gap between dot leaders and the page number, and check long titles, different digit counts, and multi-line entries. Coordinate tolerance is determined by the current output precision and visual inspection.

## Uniform page style and front/back numbering

Headers, footers, and page-number positions for the table of contents and body text use this template's uniform choice. When differences are needed, configure them explicitly and accept the table of contents' first page, continuation pages, and chapter opening pages. A full-page cover may use `empty`, subject to the delivery requirements.

Switch numbering with standard commands, not by hand-editing each page:

```latex
\frontmatter
\pagenumbering{Roman} % Example: use this when the front matter in this run uses uppercase Roman numerals
% Preface, automatic table of contents, etc.
\mainmatter          % Before the first body chapter; Arabic numerals start from 1
% Body chapters and back matter
```

When a body-text fragment has no front matter, do not insert a Roman-numeral stage. Check whether native pagination behavior such as `cleardoublepage` produces unexpected blank pages; do not offset it with negative spacing. The switch points for front/back numbering are determined by the current document's actual structure.

Distinguish the original PDF physical page, the typeset PDF physical page, the typeset reading page number, and the original book's printed page number. The table of contents shows the typeset reading page number; the printed page numbers of the original terminology index follow a separate policy and must not be replaced casually.

## Compile until stable

The first read of the `.toc` may change pagination. Do not assume two passes are enough to deliver; use latexmk, or compare the hashes of aux/toc/out after each pass. The current exporter treats `--compile-runs` as a minimum number of passes, allows at most five passes for an automatic table of contents, and delivers only when the auxiliary state is stable. If the limit is reached and the state is still unstable, save the source, build logs, and pending-acceptance status; do not hide stale page numbers or reference warnings.

Auxiliary files go in a separate build directory; the source package does not include aux/toc/out/log. Recompiling after extraction rebuilds them. Only re-export/recompile is needed; table-of-contents typesetting revisions do not trigger paid recognition jobs.

## Final verification

- Reconcile the original table of contents with the real body-text headings; check inclusion order, unnumbered sections, optional-reading asterisks, and back matter, with no omissions or internal headings mixed in.
- Actually inspect every table-of-contents page; check heading fonts, indentation, long-title wrapping, dot leaders, the page-number column, and uniform page style; a compile without errors does not replace looking at the pages.
- Check each entry's visible table-of-contents page number, native heading label, and link destination. After front matter is changed to Roman numbering, do not compare table-of-contents page numbers using "physical page index + 1"; use the target page's PDF PageLabel (for example PyMuPDF `get_label()`) and reconcile with the visible footer. When an old PDF has no PageLabels, verify the real footer; do not treat an empty string as a page number.
- Check footers on all non-cover pages, front/back numbering switches, the start and end of the table of contents/body text, bookmarks, and page anchors, with no duplicate targets or extra blank pages.
- Verify that body text, footnotes, and figures/tables were not lost or distorted by the template revision. When existing visual acceptance needs to be carried over, state the compared region and file hashes; a numbering change may exclude only confirmed footer glyph boxes, and you must not crop the entire page bottom and miss footnotes.

Report the actual checked scope and results for this run's table-of-contents entries, internal jumps, and bookmarks; counts and acceptance from old files are not evidence that a new export passes.
