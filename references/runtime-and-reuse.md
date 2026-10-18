# Capabilities, Stage Identity, and Explicit Reuse

## Backend capability declaration

`opencode_job.py`, `run_page_jobs.py`, `figure_job.py`, and `check_environment.py` all accept `--capabilities profile.json`. A capability declaration is required for model execution; offline preparation may omit it. Start from `assets/backend-capabilities.example.json`; there is no model whitelist or vendor name rule. Declaration format:

```json
{
  "schema_version": 1,
  "backend": "opencode",
  "input_modalities": ["text", "image"],
  "output_modalities": ["text"],
  "tools": true,
  "images": {
    "max_attachments": 8,
    "max_edge": 2048,
    "max_pixels": 3000000,
    "max_total_pixels": 12000000
  },
  "usage_semantics": {"input": "unknown", "output": "unknown"},
  "evidence": "explanation of the limits and trial-run evidence for this run"
}
```

The numbers above are only format examples, not verified limits for any model. Every limit may be omitted, and omission does not imply unlimited capability. The declaration itself does not prove that images arrive, tools are available, or recognition quality is good; you must still run a real-page trial before execution. The optional `model` binds an explicit model; after binding you must pass the same `--model`, and it cannot be applied to an unknown default model. When using the user's default model, omit `model` and the CLI `--model`, and record the executing model from the actual session.

Before execution, check the initial attachment count, total pixels, and the single-image dimensions of all attachments/readable source images. A full source page is also a readable image; do not check only thumbnails. `max_total_pixels` covers only initial attachments; later on-demand reads, vendor scaling, and server-side actual images cannot be proven by this static check. When the limits are exceeded, first change this run's preparation strategy or adjust the declaration with evidence; do not automatically reduce sharpness or bypass the limit.

## Stage identity

An extraction run freezes the source, page range, source images/attachments, draft ready, prompt, backend, CLI executable and configuration, selected Python dependencies, and the transitive closure of local Python imports. Page/figure jobs also record the stage environment separately. The raw executing model and the "omit model to use the default" choice are stored separately.

`runtime_contract.py` tracks static Python imports within this directory, including imports inside functions. Dynamic tools, templates, model weights, and assets use their own stage manifests; it is not a reconstructible image of the whole operating system or all shared libraries. OpenCode v2 uses the actual configuration file discovery result; v1 covers only known configuration locations and explicitly reports the discovery scope. When identity changes, you cannot continue pretending the original run is unchanged.

Content extraction prompts no longer embed the LaTeX template. `typeset` template admission is still checked separately; when changing the layout, reuse validated content and re-run acceptance for the affected typesetting in the new export directory. `--resume` keeps strict same-run recovery; accepting artifacts across versions/backends/modes uses the explicit reuse below, and does not silently start paid re-recognition.

## Cross-stage acceptance

```bash
python scripts/run_page_jobs.py \
  --source /work/source.pdf --prepared-dir /work/prepared --expected-pages 1-20 \
  --output /work/reused --reuse-validated-from /work/old-jobs --reuse-only
```

`--reuse-only` does not require installing the model CLI and does not execute model calls; it requires every specified page to be reusable. It must be the latest attempt that exited successfully, did not time out, and passed structure checks, with the source, source image and attachment dimensions/hashes, complete file manifest, and current semantic validation all passing. Ambiguity, tampering, failed pages, and missing pages fail explicitly, and it does not fall back to an old attempt. A timed-out finished product must first go through independent recovery verification and cannot be treated directly as a successful cache.

The new directory saves the original contract/checkpoint/output as provenance and keeps the original model identity. Only the paths of validated `viewed_images` are relocated; the original content and styles are not regenerated; the original output bytes are saved separately. The new run has `model_invocations=0`. Reuse is not new proofreading and does not upgrade `source_checked`. Switching to `proofread` mode and reusing old content does not justify claiming that the model proofread again.

You can also use `--reuse-validated-from old --execute` to call only for missing pages, still subject to `--max-invocations`. When specifying multiple old directories, each page must have exactly one producer. Both new and old run directories are kept; you must not modify old checkpoints to fake compatibility.

## External OCR drafts

`native_text.py`, the MinerU adapter, and the importer jointly publish `contract.json` and `NNNN/page-ready.json`. Consumers verify the frozen producer contract and each output hash; consuming a saved draft does not require reinstalling the original engine or model. Re-running the producer still requires verifying the current model and environment; the two are different stages.

Other engines first save native text and native JSON, then write an import specification:

```json
{
  "schema_version": 1,
  "source_sha256": "actual source SHA256",
  "producer": {"name": "actual engine name", "version": "actual version"},
  "pages": [{
    "page": 7,
    "image_sha256": "source image SHA256 in prepared",
    "text": {"path": "engine/page7.md", "sha256": "actual SHA256"},
    "raw_blocks": {"path": "engine/page7.json", "sha256": "actual SHA256"}
  }]
}
```

All imported files must be inside the directory containing the specification and use relative paths; do not rename the coordinate system or pass off engine JSON as shared semantic blocks. Run:

```bash
python scripts/import_drafts.py --source /work/source.pdf \
  --prepared-dir /work/prepared --specification /work/engine-import/spec.json \
  --output /work/imported-drafts
```

Then pass `run_page_jobs.py --mode proofread --drafts-dir /work/imported-drafts ...`. `proofread` must have drafts; use `extract` for independent visual recognition. Raw JSON/coordinates are kept for on-demand verification, and the output must still go through source-image proofreading and shared contract validation. The importer does not install or call that engine, does not provide automatic coordinate conversion, and does not endorse external engines.

## Usage adaptation

`collect_usage.py --run /work/jobs --output /work/usage` collects metadata from the checkpoints of all attempts; it also accepts standalone page/figure `run.json` files. Prefer session message usage and avoid also accumulating `step_finish` for the same invocation; deduplicate by session/message or step ID, reject conflicts, and reused pages do not add usage.

Token semantics are provided by the capability declaration or explicit `--input-semantics` / `--output-semantics`:

- input=`uncached` or `includes_cache`; the latter can be subtracted only when explicit cache read/write counts exist.
- output=`includes_reasoning` or `separate_reasoning`; only the latter is added to get the complete completion.
- Keep unknown as null/partial; do not guess the semantics just because token numbers exist. Failures or missing metadata also remain partial.

Produce `ledger.json` and `normalized-records.json` for use by `account_usage.py --records`. Local message/step counts are not the exact number of vendor requests; `provider_request_count` stays null. OpenCode self-reported cost is not converted into actual server-side charges; real bills and currencies must be obtained separately. When usage is not complete, you cannot claim costs are complete.
