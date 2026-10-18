# Parallel Dispatch and Bounded Acceptance

## Entry

```bash
python scripts/run_page_jobs.py --prepared-dir /absolute/work/prepared \
  --source /absolute/source.pdf --expected-pages 1-20 \
  --mode extract --backend opencode --workers 2 --max-invocations 20 \
  --output /absolute/work/jobs --execute
```

When no model is specified, OpenCode uses the user's default; an explicit `--model provider/model` also works. The Codex backend must explicitly specify an actually available model. By default it only prepares; only `--execute` makes calls. JSON-only by default; `--with-markdown` additionally requests redundant per-page Markdown, which is generally unnecessary.

`--mode extract|proofread` are decoupled from templates; formal execution of `--mode typeset` requires `--format-profile --template-ready`. The small sample `--preflight-mode` allows at most 8 actual source pages and is non-production; page numbers are not limited to 1..8. The default mode is extract; proofread must provide --drafts-dir, and you cannot use the mode name to pass off independent recognition as proofreading.

`--pages` is the mutually exclusive page subset for this queue, `--expected-pages` is the complete source range; when the cover is kept separately, no cover OCR request is issued, and the sub-queue can only report assigned_complete. Different coordinators use different output; the same output is exclusively locked by a file lock. Expand concurrency only after the first real ready page passes the CLI/structure preflight; use `--no-preflight` only for paths already verified separately, and it does not skip typeset template admission.

## Producers and consumers

- After finishing a page, the image producer publishes the complete page images/attachments/hash manifest; only after the full range is complete does it publish the final manifest. progress or partial ready does not prove the whole book is complete.
- Local OCR runs resident once, publishing raw blocks, draft, assets, and page-ready per page. native_text also uses the same ready protocol. ready contains at least the source, source image, and model or adapter identity, plus the hashes of original.md and raw-blocks.json; additional assets are also registered. On failure, do not publish a fake draft.
- The scheduler checks image and draft readiness first, then submits a limited number of workers; waiting for drafts does not occupy a worker. There is no serial barrier for the first/second half of the book, and image preparation/local OCR/visual proofreading can overlap in a pipeline.
- `--producer-timeout` is the continuous wait limit while the worker is idle and the producer makes no progress; normal model processing and queued ready pages do not count. `--timeout` is the limit for a single CLI process group. When the producer fails, save incomplete, leave a trace at the end of the current invocation, and do not fake complete.

## Identity and budget

The contract freezes the source, range, prompt snapshot, model selection, configuration, and actual CLI and tool hashes. The file configuration of the default model is included in the snapshot; when the configuration does not explicitly give a model, preserve the fact of "using the default", determine the executing model from the actual session response, and do not claim the name is fixed.

The page-level fingerprint includes the source image, attachments, draft, native blocks, and ready identity for this page. Recovery verifies all artifact hashes and re-validates the structure, and does not fall back to an earlier successful attempt:

```bash
python scripts/run_page_jobs.py ... --resume --execute
python scripts/run_page_jobs.py ... --resume --retry-pages 7,12 --execute
```

By default failed pages are not retried; `--retry-pages` explicitly allows one new attempt for the specified pages; currently at most two attempts per page. Do not fake recovery by modifying old artifacts/contract. When content is unchanged but the template changes, reuse the original content, run acceptance in the new typesetting directory, and do not require re-running old OCR.

`--max-invocations` limits the total number of CLI invocations across the queue history and new attempts (failed invocations also count). When the limit is reached, save the unfinished pages first; continuing requires explicitly raising the quota. One CLI invocation may make multiple API requests, and it must not be passed off as a cost cap or an exact request count. Mark service bills/counts that are incomplete as partial/null; separate different currencies, see account_usage.

## Ownership and two-level checks

During extraction, body text and image inventory can be processed at the same time; drawing reads read-only original page context as needed and does not guess body data. The typesetting stage uses semantic and figure asset snapshots; mutually exclusive chapters can be trial-typeset first, and tasks with missing dependencies stay pending integration.

Each chapter/semantic block/asset has only one writer per round. The original PDF, page images, raw and published snapshots are read-only. One owner maintains the template; other tasks submit patches/new assets, and the coordinator merges them; multiple workers must not modify shared semantic files or products at the same time.

Local loop: produce → actually compile/render → look at the original page and the new product → revise → re-render and re-verify. After root integration, check global pagination, table of contents/references, fonts, and combined figures and tables, then dispatch fixes by mutually exclusive issue. When a restricted worker cannot execute, the coordinator renders and returns the actual preview; this must not be used to skip looking at the images.

By default, at most 3 rounds after the local first draft and at most 3 rounds after the root first review; the round count is not reset by new issues or template changes, and the user's budget can override it. An issue has at least the source page/block, output page/region, owner, acceptance criteria, round, and current PDF identity. Fix template-wide problems uniformly first, then re-verify the affected parts. When the time, invocation, or revision limit is reached, keep unresolved items and needs_review, and do not call it passing.

produced means written to disk, validated means structure passes; content source confirmation and visual passing are recorded separately. Report wall-clock and cumulative job time, checked and unchecked scope, actual revision count, and stopping reason; do not just aggregate worker "done" messages.

## Explicit recovery of a timed-out finished product

A timeout or non-zero exit still records needs_review, and does not retry automatically or rewrite the checkpoint. If the process has ended and the output is complete, an independent coordinator can, after actually checking the source image, pass --recoveries receipts.json to canonicalize_pages.py. Each page gets one receipt containing page, source_sha256, source_image_sha256, checkpoint_sha256, output_sha256, decision="accept_complete_output", reviewer, reason, evidence_image, and its evidence_image_sha256. The path is either a source image readable in this run or the crop image that was actually checked; tools compute the hashes, and the model does not guess them. After verifying the original file manifest, source, end state, and complete page structure, the recovery is recorded only to the new normalized product; the old timeout/exit code, raw JSON, and uncertainties remain unchanged, and content evidence is not upgraded either. If the check is incomplete, revise for the problem or explicitly retry.

The page queue can use --prompt-template to specify the task prompt for this run, which must still contain the page identity and the complete base JSON contract; the file content and the expanded final prompt are both frozen into the contract. Changing the prompt needs a new run; instructions must not be changed silently during recovery. The native layout JSON is on-demand context, and you do not need to re-read the full text of every page; images and the body draft are the basis for proofreading.

Capability configuration, external draft import, the complete stage identity scope, --reuse-validated-from/--reuse-only, and usage adaptation are covered in [Runtime and reuse](runtime-and-reuse.md). The current runner contract version is 3; old contracts remain, cross-version use requires explicit reuse, and they are not hand-rewritten to the new version.
