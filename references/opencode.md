# OpenCode Invocation

`opencode_job.py` prepares single-page recognition/proofreading; `run_page_jobs.py` handles the bounded queue. Omitting `--model` uses the user's configured default model, without hardcoding a vendor or model; an explicit model uses `provider/model`. Do not modify global default settings.

```bash
python scripts/run_page_jobs.py --prepared-dir /absolute/work/prepared \
  --source /absolute/source.pdf --expected-pages 1-3 --mode extract \
  --backend opencode --capabilities /absolute/work/capabilities.json --workers 2 --max-invocations 3 \
  --output /absolute/work/jobs --execute
```

Executing page/figure workers requires `--capabilities` declaring image input, text output and tools. This declaration does not replace a real image/tool pilot. The command is within the authorized scope; without `--execute` it only prepares. Expand concurrency only after the first real page passes contract validation. Image input is prepared as overview, native tiles, and crops according to the current service's capabilities; do not copy the area/edge limits of historical models.

## Version adaptation

`opencode_runtime.py` determines the configuration and CLI syntax from the actual `--version`, and fails explicitly on an unknown major version. V1 uses a single OPENCODE_CONFIG_CONTENT, permissions, and `run --pure --dir`; V2 uses `agents/permissions` in `.opencode/opencode.json` under the job directory, and invokes `run --standalone` with the job as cwd. The independent server reads this job's settings and does not restart the shared service. Both routes restrict workers to read input and write output, and forbid shell, network, and recursive agents; drawing allows an additional read-only shared image range.

V2's execute is the Code Mode tool entry point; its internal read/edit/shell are still checked by their respective rules. Permission configuration, CLI version, and configuration file hashes go into the run record; configuration discovery output may contain credentials, so the program saves only file identity and default model fields, not the complete configuration. When the default model is not explicitly given in the configuration file, the actual model is determined by the session response; do not misrepresent "using the default" as having fixed a certain model.

The optional `--model-options` accepts only the allowed fields of reasoning/provider, and must specify a model so that the options do not fall through to an unrelated model. Whether options actually take effect must be verified against the actual provider/version.

## Single page and revisions

```bash
python scripts/opencode_job.py --manifest /absolute/work/prepared/manifest.json \
  --page 1 --job-dir /absolute/work/one-attempt --capabilities /absolute/work/capabilities.json --json-only --execute
```

Proofreading can add `--draft` and `--draft-json`. Each attempt uses a new directory, and the original model result is not overwritten; `--resume --retry-pages` only allows explicitly specified retries. For font tasks without images, create separate input based on font references; do not fabricate viewed_images.

By default each invocation times out after 900 seconds, and an OpenCode worker has at most 24 steps by default; steps on the Codex backend do not mean the same CLI step limit. The producer timeout only counts waits when the worker is idle and the producer makes no progress; normal model queuing does not count against this quota. Error records save a redacted category; complete configuration/authentication/image base64 is not written into diagnostics.

## Verification and cost

CLI image paths, attachment manifests, and a model's self-reported viewing do not prove the server received the same pixels. Check the attachment/read success status in the actual session, the image dimensions, and any available hashes; a read failure does not mean the source image check is complete.

Keep the session ID, returned usage, model/provider identity, and elapsed time. A CLI invocation may contain multiple service requests; after a timeout the remote side may still bill. When returned fields are missing or their semantics are unclear, keep partial, normalize according to the service's current definition, and pass to `account_usage.py`; do not guess costs.

Text quick screening only provides candidates and does not replace source-image adjudication. Grouping of overly long chapters must cover the complete range of this run; do not mark context overflow, failed retries, or unchecked parts as passing.

Version syntax basis: [V2 CLI](https://opencode.ai/v2/docs/cli/commands/), [V2 configuration](https://opencode.ai/v2/docs/config/), [V2 permissions](https://opencode.ai/v2/docs/permissions/). The runtime still follows the installed version and the first real page.

After a V2 invocation ends, the entry point automatically saves actual_models and per-assistant session_usage (unique message ID, returned tokens, OpenCode reported cost, finish) through the local session export, and does not save the exported body text/images. The executing identity of the default model can be verified from this. Keep the raw token fields of each version; reasoning may be independent of visible output, and do not add them together arbitrarily or claim an inclusion relationship. Session statistics also do not equal vendor billing; timeouts and incomplete messages stay partial.
