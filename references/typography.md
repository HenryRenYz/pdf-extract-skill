# Font calibration driven by in-book rules

For content extraction, AI retrieval, and personal study, the default sequence is "proofread content against the source image → review in-book rules → independent font inference → review uncertainties". TexTAR is not a default dependency; enable it only when the user specifies it or a new held-out evaluation shows benefit. The applicability of rule transfer must be verified with held-out samples from the current document.

## Establish and freeze this book's rules

Summarize rules from representative pages that contain headings, terminology emphasis, scalars, vectors/matrices, components, and units, and also select regular-font negative examples. Keep learning pages and held-out pages separate; the specific page count depends on how much the document varies and on the budget.

AI may draft the induction, but the original rules must be reviewed against the source images, checking regular-font negative examples and emphasis exceptions in particular. Do not treat AI self-report, TexTAR candidates, or agreement among multiple models as review evidence. Save both the original induction and the reviewed version; the rules include:

- `source_sha256`, `version`, `fit_pages`, review status, and review record.
- Each rule's `id`, applicable chapter/context, description, verified examples, exceptions, and source page/bbox.
- Which judgments are transfer inferences and which scopes are unevaluated; do not extend this book's notation conventions to other books.

Run the held-out test only after freezing a version. If rules are revised after seeing test results, generate a new version and find separate held-out samples, keep the old version's scores, and do not use the revised rules to claim a perfect score on the original blind test.

Review can start from the following categories; they must not be treated as fixed rules for every book:

| Category | Conditions that must be distinguished |
|---|---|
| Headings and structural labels | Heading level, definition/example/solution labels versus the same words in ordinary sentences; record headers separately |
| Chinese terminology emphasis | Verified definition positions versus ordinary repeated mentions; "first occurrence", "this section introduces", or "with an alias" does not by itself prove bold |
| Math scalars/functions/indexes | Usually math italic; named operators and units are judged separately; the meaning of the same letter can change |
| Matrix/vector body | Infer from glyphs and semantics verified in this book; do not make something bold merely because it is uppercase or subscripted |
| Components and elements | Distinguish a whole column vector `a_i` from a scalar element `a_ij`, and a vector `x` from a component `x_1` |
| Units/operators/transpose | Handle units versus same-named variables, and named operators versus function symbols, separately; follow the confirmed conventions of this book |
| Special math fonts | List double-struck and similar separately; do not simply classify them as bold; do not guess an unknown font family/font size |

Symbols for the same concept, even in the same formula, may have source weight exceptions. Rules cannot prove that every position in the source book is consistent; faithful restoration and typesetting normalization are different deliverables, and when the user asks for the former, do not automatically apply the latter.

## Independent rule calibration

Complete source-image proofreading of text and formula content first, keeping the raw OCR and the revised version. The independent font job reads the revised blocks, stable IDs, source text, bbox, chapter/local context, and the frozen reviewed rules. Body text, sub/superscripts, and symbol meaning must not be changed along the way in this step; font judgment depends on content recognition, so when semantic identity is unreliable, list an uncertainty first.

The rule stage may omit images or TexTAR. Use `assets/style-prompt.txt` and output `style-patches.json`; every assigned block returns its style and needs-review status. Fragment identity and semantics in the input come from actual OCR/context extraction; do not treat manually supplied localization and semantic descriptions in an experiment as proof that production capability has been verified.

Record evidence for style and body text separately: the block's `evidence` states the basis for content recognition; `styles[].evidence` can be `rule_inferred`, with `rule_id` and `rule_version`. When a rule is unknown, out of scope, or conflicting, do not automatically bold all terminology and do not unify all matrix symbols; keep confirmed styles and output needs-review items. An empty style does not mean regular font is confirmed.

Locate patches by stable block ID, input body text SHA256, and Unicode character ranges. The calling agent computes and supplies `base_text_sha256` first; the model only returns it and does not guess hashes. During merge, verify the source hash, rule version, block text hash, and ranges, and reject stale patches, content rewrites, or conflicting ranges for the same property. Regular, bold, italic, and bold italic must be distinguishable; do not collapse double-struck and similar into these four classes. For fields, see [Data contract](data-contract.md).

Generate native LaTeX from the style metadata: body-text emphasis is expressed through the template's font mapping and standard font commands; use `\textit` for italic and combine commands when needed (the actual glyph for Chinese emphasis is determined by the current document profile); math scalars, bold italic, bold upright, and `\mathrm`/`\operatorname`/`\mathbb` are expressed separately; optional Markdown then uses asterisk emphasis. Prefer configuring font packages; do not define a separate font macro for every position. Do not put plain-text asterisks into formulas, and note that bold upright must not be changed to bold italic merely because the content is a "matrix". Do not decompose LaTeX commands for per-character candidates that the model repeats for the default math italic or already encoded units; when reliable mapping is impossible, keep the candidate and the uncertainty in the working sidecar and do not fake confirmation. A font that the source confirms but no math macro yet expresses requires an explicit macro revision before export. If the render index changes, recompute it or keep the source mapping.

Run independent calls in parallel by page or semantic block, with isolated input/output directories, granting only the necessary read/write permissions, and do not enable shell, network, or internal subagents additionally for a font job. A job without images must not fill in a false `viewed_images`; the final page record retains the real viewing record from the earlier content proofreading and saves the font job's run record separately. `opencode_job.py` does not offer an image-free font mode; do not send style patches directly to the page-level validator and pass them off as complete page output.

## Font hierarchy for headings and the final render

Identifying a heading level does not mean the font size is already correct. Centralize the render strategy for body text, each heading level, structural labels, figure/table captions, and in-figure text in the LaTeX template, using the document class and font/heading package settings; keep only semantics at the body level, and do not insert font size, centering, or spacing commands item by item. Confirm heading levels, alignment, font-size differences, and numbering spacing from the source pages and the delivery target, and write them into the current template.

When the source scan cannot precisely infer font point size, record the chosen font size and its basis, and do not pass the chosen value off as a measured value from the source book. During acceptance, verify the final PDF's actual font size and font family, and their relative hierarchy against the source page; in particular, check whether the document class's body Chinese font size matches presets such as `normalsize`, to prevent a level-3 heading from being smaller than body text even though it uses a preset size. After unifying the heading style, check short headings, long-heading line breaks, and the actual centering of numbered headings.

In-figure text is also part of reading-font acceptance: check symbol size from the figure size after final embedding, not from a standalone `fontsize` in the script. When readability must be improved, adjust the frozen whole-book figure size/font strategy first, preserving each figure's relative scale and composite layout; never stretch a figure containing small text arbitrarily to fill the page on its own. For specific scales and composite-layout limits, see [Figure workflow](figures.md). The provenance of headings, symbols, and numbering content stays in the semantic data; font normalization does not authorize changes to body text or source-book numbering.

Set in-table text and formulas according to the whole-book readability strategy; do not replace column-width and layout adjustments with whole-table scaling. Configure table caption font, spacing, and line width uniformly in the template; full width or natural width, source proportions, and table-text wrapping come from per-table reviewed source metadata, so do not stretch every narrow table to fill the type area because of unified fonts, and do not compress every full-width table into a narrow table. Use native fixed-width columns for full width/source narrow width and verify equal division or source proportions; use `tabular` for natural width; use centering declarations and outer padding, and do not fill the inter-column glue and mislabel it as equal width. Use standard mechanisms such as `wraptable` or `minipage` with `captionof` for simple side placement. The final render audit also checks in-table text/symbol size, table width, and the complete paragraph beside it; do not check only whether the text font matches. Per-table dimensions come from source metadata; never hardcode them in the template by figure number.

## Uncertainties and source-image review

A new chapter, a new term, a first naming, an unfamiliar symbol, unclear scalar/vector semantics, a conflict between raw OCR and the rules, or inconsistent weights on the same page can all trigger verification. Not every "first definition" is necessarily bold, and not every vector body matches the rules.

Crop native pixels from the real bbox, retaining the whole line or the definition context and confirmed regular/bold glyphs for comparison; use overviews for localization. Enlargement can aid observation but does not add scan detail. The verification capability of local crops also needs validation; do not treat a model's new judgment as an automatic fix.

A model's `source_checked` after viewing an image is only self-report; upgrading evidence requires an actual image-input audit and human/independent source-image verification. When confirmation is still impossible, deliver `uncertain`, the reason it needs review, the source page/bbox, and an image link, and do not fabricate a precise font conclusion. Do not clear an uncertainty just because all models agree.

## Optional TexTAR route and trade-offs

Enable it only when a comparison is needed or new samples show it is useful. The user or this configuration supplies the weight location; do not hardcode a machine path. Retain the official code revision, weight hash, preprocessing, and detection-box provenance; see the [official code](https://github.com/tex-tar/tex-tar) and [weights](https://huggingface.co/Tex-TAR/model_tex-tar), and verify actual dependencies by version.

TexTAR is a candidate font-attribute classifier; it does not replace body-text OCR. Evaluate automatic text-box coverage first, then classification given accurate boxes; a test with manually localized boxes must not be called an automatic-pipeline score. Record the 125-word box condition driven by the release version, short-sequence adaptation, and detector differences. Its probabilities that were not calibrated on this book cannot be used directly as automatic pass thresholds; red/blue and similar color overlays mark candidates only, not ground truth. The benefit of feeding color annotations to a vision model requires a separate held-out test.

When comparing rules, TexTAR assistance, and a hybrid, fix the content draft, fragment identity, context, and model, and freeze the learned rules; report differences in image visibility. To attribute TexTAR's incremental benefit, you also need a control that sees the same source images without TexTAR. Without that control, do not attribute the hybrid group's advantage to TexTAR itself.

The choice is based on benefit on unseen pages and regular-font negative examples, false-bold/false-italic rates, latency, cost, and maintenance burden. If the hybrid is close to rules, keep the simpler rules default; introduce the hybrid only for a clear improvement. Do not assert that the two are equivalent across the whole book based only on equal scores on a few samples. Keep historical experiments in the development test directory; operation does not depend on old-book results.
