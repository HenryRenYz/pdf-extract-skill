# Quality, time, and cost

When typesetting is needed, first use [format preflight](book-format-preflight.md) to freeze the rules and real samples; ordinary extraction checks only content and the semantic contract. When there is code or formal grammar, use [code and special text](code-special-text.md) to verify the specific package and structure. A known style/structure loss must not be filed merely as an ordinary character-recognition uncertainty and then declared a typesetting pass.

Typesetting acceptance lists source layout relationships separately: heading font-size hierarchy/centering/numbering spacing, the wide spacing of Chinese characters after a structure label, the actual font family/weight of Chinese emphasis and its separation from math bold, table outer/inner/local/double rules, caption spacing, and table width, proof end markers, side-by-side figures and the left-right relationship of figures and text, readability of symbols inside figures, caption attribution, cross-reference jumps, and full-page display of the cover. Correct table data alone, or all figures being bound, does not replace ruling-line/layout reconstruction. The user chooses the cover mode/scope; a source-image cover both preserves the whole page and needs no OCR confirmation.

## Gold standard and comparison

The same PDF, the same physical page range, and the same source images are used for all candidates. Direct visual OCR must not read other candidates' drafts or the gold standard; "OCR + proofreading" may read the corresponding draft but must not see the answer. Record the real image dimensions, crops, and processing differences. Save outputs immutably and keep the first output and the repaired version; after recovery, do not show only the best run.

A human establishes an explicit set of checkpoints from the source images, noting the page number, bbox, and expected result for each item. Cover:

- Chinese characters, missing lines, order, and cross-page sentence breaks.
- Inline/display formulas: superscripts and subscripts, signs, Greek letters, tildes, transpose, parentheses, integration limits, variable glyphs, and the original numbering.
- Each table cell's numbers, units, merge relationships, and arrow count/direction; do not evaluate only whether the table exists.
- Heading level, bold/heavy weight, local emphasis, body-text italic, bold vectors/matrices, upright functions/units; also check ordinary-font negatives and over-emphasis.
- Whether images, captions, labels inside figures, and references correspond.

Record strict matches and acceptable equivalent LaTeX forms separately. Do not call similarity/CDM/TEDS or the pass rate of a few checkpoints "whole-page accuracy". State the denominator separately for text CER, formula exact-match rate, style precision/recall, and table cell-by-cell accuracy; when the sample is too small, give only "N/M checkpoints passed". Write not evaluated for italic, vector, and font-family cases not covered.

A model proofreading its own draft tends to keep its own missed recognitions; agreement from another model is also not the gold standard. A formula that compiles does not mean its symbols match the source image. Run file/structure checks first, then check body text, math, fonts, and figures and tables in the actually compiled LaTeX PDF against the source images; for optional Markdown/HTML, run acceptance separately in the corresponding viewer. When actual preview is impossible, state that this step was not done.

## Full-scope layout audit

First inspect the overview of all source pages and the final PDF in the current processing scope, map semantic blocks to source pages, and do not assume that page numbers in the typeset PDF match the original pages; then zoom in on all key areas such as tables, figure groups, heading hierarchy, proof endings, and numbering exceptions. A whole-book job covers the whole book; a sample job covers the actually specified scope. An AI audit must be able to access the original pages and the final rendering, not just read LaTeX or the model's own completion report; chapters can be audited in parallel, recording the checked scope and the source blocks/output pages found. List unchecked parts explicitly; they must not be called a full-scope pass.

| Check target | Items to verify in the source pages and final PDF |
|---|---|
| Headings | The level supported by the source, centering, and line breaks; the actual output font size relative to the body text. Verify the heading alignment, hierarchy sizes, and numbering spacing frozen for this run; do not just look at presets such as `large` written in the template. |
| Fonts and structure labels | Chinese emphasis uses the frozen font family/weight, and math bold keeps the math mechanism; the wide spacing of Chinese characters after the source labels "例/解/证明" (Example/Solution/Proof) is consistent, and ordinary uses of the same words are not mistaken for structure labels. |
| Numbering and references | The printed number in the source book, the counter result, and the stable `label` correspond one-to-one; unnumbered formulas get no new number. The template unifies number appearance/alignment; primes or skipped numbers in the source have a verification record. References display correctly and clicking actually reaches the corresponding chapter, formula, or figure or table; do not accept based only on the absence of undefined-reference warnings. |
| Proof endings | The square belongs to the right proof and sits at the right end of the last line; a formula ending is placed with the standard mechanism and must not be isolated as an ordinary paragraph at the left end of the next line. Keep all proof body text and its source mapping. |
| Tables | Cell data/merges and line topology are each correct; the table caption and table body have no large blank space caused by nested environments. For each table, confirm against the source page whether it is full-width, narrow, or wrapped around text, and whether column widths suit the actual content and the content is centered; equal/unequal width relationships in the source can serve as an initial reference; outer margins, column boundaries, and arrow helper columns must be checked, do not treat inter-column filling as equal division, and do not stretch the whole table or leave the whole table unstretched; keep the per-table confirmed narrow-table and wrap-around relationships. Text/formulas inside the table use a unified font-size strategy; both source proportion and natural width are constrained by final readability and what the type area can hold, and you must not force-shrink text just to fill width. Every table must be checked for avoidable line breaks: when a short `n`/sequence column has slack while a long header/formula wraps, redistribute width; compare table font size at the same reading scale, and narrow tables/wrapped tables must not suddenly become smaller. You must look at the final product, not just review column weights. Local rules or double rules are not replaced by a global grid. The source uses standard table and layout mechanisms; the width decision records the basis for the source layout and the fit to the finished content, and complete paragraphs / figure and table captions / sources and reference jumps are all kept. |
| Figures | The figure size is readable in the final PDF, and letters/formulas inside are also readable; uniform scale adjustments preserve each figure's relative size, side-by-side figures remain side by side, and figure-text side placement preserves the relationship supported by the source as much as possible. Enlargement does not exceed the type area or force part of a group to shrink excessively. Check real occlusion, white backgrounds hiding lines, captions, and the link to body text. |
| Cover | The user-selected whole-page source image or concise metadata method is correct; handle only the cover role verified within scope, and whole-page preservation adds no page number overlay or element-by-element OCR. |

When the audit finds a common deviation, first change the template/package settings and re-render the affected scope; make a recorded local exception only for a special case of the source book itself. Prefer mature packages, the native environment, and common commands; do not treat a batch of local font sizes, manual spacing, or repeated custom macros as a global fix. After revision, report the layout items actually verified and the remaining limitations; a visual layout pass does not mean that text/formula recognition accuracy has been proven against the gold standard.

## Local and integrated revision acceptance

The worker first completes the render self-check loop for its chapter/asset, then root assembles the actual PDF and performs the final review. Consolidate problems found in the first review into a unified issue list, then dispatch parallel revision rounds by global/local and mutually exclusive owner; after re-integration, check every fix and every affected area, and repeat until there are no unresolved items or the limit is reached. By default there are 3 revision rounds after the worker's first draft and 3 integrated revision rounds after root's first review, and the budget can cover this; for scheduling and the write contract see [parallel workflow](parallel-workflow.md). Checks must target this run's final PDF version/hash; fixing the source without looking at the new render cannot close an issue.


The acceptance report states the checked scope, the actual number of revision rounds, closed/unresolved issues, the product version, and the reason the budget stopped. If problems remain at the limit, the status is `needs_review`/pending acceptance, keep the usable candidate and list the specific defects; do not call it "passed" or "looks fine". A full-scope layout pass also does not mean that text/formula accuracy has been proven against the gold standard.

## Comparing font rules and auxiliary models

Keep rule-induction/review pages separate from held-out pages, freeze the rules and human reference first, then run the rule-only, TexTAR-assisted, and hybrid comparisons. Body-text drafts, snippet identity/location, semantic context, and model are the same, and record whether each group saw the source image. Save the automatic induction and review versions separately; one-time review time and failed-call cost also count toward the process.

Report strict match for the four classes upright/bold/italic/bold italic separately from precision/recall for "contains bold attribute" and "contains italic attribute"; list uncovered categories such as double-struck and ordinary body-text italic separately. Evaluate classification capability on automatic box coverage separately from that on given human boxes. Font checkpoints that a human selects and annotates semantically measure only conditional classification and cannot claim end-to-end OCR, production semantic localization, or whole-book font coverage.

Negatives include later ordinary mentions of a term, "本节介绍" ("this section introduces") in body text versus a bold heading, a matrix itself versus scalar components, and source weight exceptions for the same symbol. Report counterexamples chosen specifically after seeing results separately; after revising the rules based on them, test again and do not mix them into the original blind-test score. Only an ablation with multiple groups all seeing the image and differing in whether TexTAR is included helps judge TexTAR's incremental benefit; a difference between rules-only-without-image and hybrid-with-image is not enough for attribution.

A tie on a few checkpoints does not prove whole-book equivalence. Rules can start as a simple default, but report the risk for unseen chapters and the uncertainties; local zoom, a model's self-reported source_checked, and a high confidence probability cannot replace source-image verification. For the process see [font calibration](typography.md).

## Elapsed time

Record download, rendering/tiling, layout preprocessing, model loading, recognition, proofreading, human verification, and retries separately. For parallel runs, report total wall-clock time, concurrency, per-page latency, and total job time; do not add job times and call the sum total wall-clock time. Timeout/interruption/rerun costs are part of the actual process and must not be discarded. Time from configuration changes, upload problems, and mixed recovery cannot be used to claim that a model itself is slower.

## Usage and cost

Normalize to the following fields, then aggregate with `scripts/account_usage.py`: input_uncached, cache_read, cache_write, output (the full completion including reasoning), reasoning (a subset of output). If an API's input already includes cache, first subtract the cache read/write counts the API explicitly returns; when this cannot be confirmed, mark it partial, do not guess.

Some OpenCode versions store visible output and reasoning separately; add them to get the full output only when adapting. OpenRouter's native completion already includes reasoning, so do not add it again. Count every API request rather than only the final text; repeated context, subagents, failures, and cancelled jobs may all be billed. Keep a unique request/generation/message ID for each record to prevent session/export double counting.

For DeepSeek and similar, estimate with the official input, cache, and output unit prices at call time, and record the currency and price date; official CNY prices and official USD prices must not be mixed into an exchange-rate estimate. For OpenRouter, query the generation's total_cost/usage.cost first (in the currency of the returned field), which differs from OpenCode's static model-catalog estimate. A cancelled stream may have no final usage; try a follow-up lookup by generation ID; if it cannot be completed, mark it partial and list the known cost, without calling it an exact total cost. The actual cost is the request cost returned by the service and does not equal a reconciled account charge.

Model-internal subagents and CLI concurrency can stack, and the budget counts all requests. Do not store API keys, request authentication, or image base64 in audit files; keep only metadata such as ID, model/provider, usage, cost, dimensions, and hash.

Display-formula spacing audit: compare the actual vertical gaps above and below for a simple formula, a fraction/multi-line formula, a short/long lead-in sentence, footnotes, and narrow columns; do not check only the skip variables.

## Acceptance for new capabilities and artifact admission

Record test results and visual conclusions separately. For the shared structure chain, verify that code/grammar/display_group and underline/kai are not downgraded/dropped during validation, normalization, assembly, export, or retrieval; the program's original UTF-8, style Unicode ranges, caption/annotation attribution, and single consumption are deterministic checks. Paragraph display-formula continuation / a true new paragraph negative, local heading levels, and list labels/nesting need a comparison of real source images with the finished product. A right-side brace is limited to non-wrapping lines, grammar only to explicit native structure, and anything beyond the capability remains needs_review.

`format_preflight.py` uses real files and hashes to reject changes in template/tool/source evidence/actual PDF identity, missing checks, unknown status, missing representative pages, unresolved issues, and fake PDFs; this proves the run contract is verifiable and does not replace a judgment of source accuracy. ready.source_checks' reviewer, physical source pages, actual output pages, and real readable evidence cover the test pages; family_coverage covers the book's actual tested_families. An empty JSON self-reporting accepted cannot be admitted. Preflight mode allows at most 8 actual source pages and sets production=false; do not describe a small-sample pass as a whole-book quality pass.

## Reproducible per-item checkpoint evaluation

`scripts/evaluate_quality.py` reads independent source-image checkpoints, candidate pages, and an independent alignment file, and outputs five groups of results, **without producing a composite score**. Evaluate the first output and the revised version separately; any reference, equivalent form, or parsing adaptation added based on these results is recorded separately as a post-hoc revision and not mixed into the originally frozen baseline.

```bash
python scripts/evaluate_quality.py --reference qa/reference.json \
  --candidate canonical/pages/0007.json --alignment qa/alignment.json \
  --manifest prepared/manifest.json --output qa/result.json
```

The reference object contains `schema_version:1`, `reviewer`, `page`, `source_sha256`, `source_image_sha256`, `evidence:{path,sha256}`, and `checkpoints`. evidence points to the actual complete prepared source-page PNG; the path is relative to the reference, and the hash must match that page's source image. Checkpoint ids are independent of candidate block ids, for example:

```json
{"checkpoints":[
  {"id":"sentence","dimension":"text","text":"literal source sentence"},
  {"id":"equation","dimension":"formula","text":"x+1","accepted_variants":["x + 1"]},
  {"id":"grid","dimension":"table","cells":[["A","B"],["1","2"]]},
  {"id":"weight","dimension":"styles","text":"term","styles":[{"start":0,"end":4,"kind":"bold"}]},
  {"id":"flow","dimension":"reading_order","units":["sentence","equation","grid"]}
]}
```

alignment contains `schema_version:1`, `reviewer`, `reference_sha256`, `candidate_sha256`, and `units`; each checkpoint maps to `{block_id,start,end}`, and Unicode start/end can be omitted, meaning the complete block. Omit the mapping for unrecognized regions; do not match unrelated identical words to pad the count. An independent reviewer determines the source-image regions; do not use fuzzy search based on the answer to fake model localization capability. The tool verifies identity and spans but cannot prove that the reviewer was actually independent or that the candidate really corresponds to the source region.

- Text: strict Unicode exact-match count, edit distance / reference character count (CER); a missing passage counts as a whole-passage deletion. Spaces, ligatures, and punctuation are not globally normalized; added characters can make CER greater than 1.
- Formulas: count strict matches and acceptable forms explicitly listed in advance separately. There is no automatic TeX semantic equivalence judgment; unlisted whitespace/macro-form differences can also mismatch and must be explained separately from actual symbol errors. Do not therefore directly call it a math error rate.
- Tables: shape, reference/candidate cell count, exact text match at the same coordinates, text and rowspan/colspan matching together, plus a separate count of parse failures. Extra candidate cells must not be hidden by the reference-cell denominator. This evaluates only data and merge relationships, not ruling lines or layout.
- Styles: first require the aligned text to be exactly identical, then count Unicode character-level TP/FP/FN and precision/recall for each kind; also count bold/italic attributes (including bold_italic). Report the coverage of reference characters / actually scored characters and the number of checkpoints that pass completely. Misaligned text or unrecognized style regions do not enter conditional precision/recall, and this must not hide missed recognition.
- Reading order: count reference units, aligned units, all reference pairs, comparable pairs, and in-order pairs separately. A missing unit does not count as correct; overlapping regions in the same block must not be faked as correct order. The in-order rate covers only the checkpoint relationships that were specified.

Freeze the reference before use, then have a worker who has no access to the answer perform recognition; alignment and source review are done by an independent coordinator. The test directory `tests/fixtures/quality/` stores self-authored synthetic source images, deliberately flawed/reviewed candidates, relative-path references, and expected counts; this set is regression material and must not be distributed to extraction workers. Store held-out tests on new, unfamiliar documents separately and do not directly upgrade them into a whole-book quality conclusion. A group of checkpoints with 0 means not evaluated, not zero errors.

The shared table parser supports structured row arrays, explicit 0-based row/col cells, HTML, pipe tables, and restricted literal `tabular`; the last reads only explicit cells and row separators and does not execute TeX macros. Macro-generated rows, nested environments, complex multirow/multicolumn, or row-spacing modifiers must use explicit cells, not guessing. The original LaTeX is still stored in the source block.text; literal-format parsing does not mean the source-image ruling lines or fonts are confirmed.

For usage, first connect run records with `collect_usage.py`, then aggregate with `account_usage.py`. The aggregated `tokens` stays null when any corresponding record is unknown; list known subtotals and field coverage separately; do not pass off local assistant message/step record counts as provider request counts. For capabilities/definitions and cross-stage reuse see [runtime and reuse](runtime-and-reuse.md).

For supported source styles, verify both the retained semantic value and its visible realization (for example RGB ranges, grammar indentation and script position). Source graphic inventory coverage is checked independently of the OCR figure list, including fragments at page boundaries. Report coverage, transcription accuracy, and layout acceptance separately. When a shared exporter capability changes, replay identical semantic inputs across the affected template families; retain the previous products and fix only the demonstrated differences. Keep document-specific coordinates and expected text in test evidence, not runtime prompts or general rules.
