# PDF Extract Skill

Extract text, formulas, tables, and figures from PDFs or numbered page images, with source references. Proofread against the source and optionally reflow the result into editable LaTeX and a reading PDF. Original outputs, source locations, and uncertainties are retained for review.

This is an experimental agent skill. It requires a coordinator agent that can view images and run scripts. Installing the Python dependencies alone does not provide a standalone service for processing entire books. Formulas, fonts, and complex layouts still need source-image review.

## Installation

The project is primarily used on Linux. Its scripts depend on POSIX process groups and file locks. macOS has not been fully tested; use WSL on Windows.

After downloading or cloning the repository, run these commands from its root directory. Python 3.11+ is required; local validation has used Python 3.12.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

To make the project available as a Codex skill:

```bash
mkdir -p "$HOME/.agents/skills"
ln -s "$(pwd)" "$HOME/.agents/skills/pdf-extract"
```

If a skill already exists at that location, decide which version to keep before creating the link. Start a new session and invoke `$pdf-extract`. Ask the agent to run scripts with the project's `.venv/bin/python`. The pip installation supplies dependencies; the skill's scripts, templates, and instructions remain in the project directory.

Other environments that support Agent Skills can read [SKILL.md](SKILL.md), but they have not been individually tested.

## Recommended configuration

The following combination is currently used. It is a recommended setup, not a model whitelist enforced by the runtime.

| Role | Configuration |
|---|---|
| Orchestrator | GPT 6.1 Sol for planning, organizing semantic data, review, and integration |
| Worker | DeepSeek V4.1 Flash through OpenCode for page transcription, proofreading, and assigned subtasks |
| Local client | OpenCode v2.0.32; the runtime also has a v1 adapter and rejects unknown major versions |

**Every participating model must support image input and the tool calls required for its role.** A model name alone does not establish image support through a particular provider. Before using a new model or provider, test one real page to verify image access, tool use, and output. A capability declaration does not prove that images reached the model.

Follow the [OpenCode documentation](https://dev.opencode.ai/v2/docs/models/) to install the client, connect a provider, and select a model. The [minimal configuration example](assets/opencode.example.json) uses `your-provider/your-model-id`; replace it with the identifier for your actual provider. Display names are not necessarily API IDs. Keep credentials out of the repository. When `--model` is omitted, this project uses OpenCode's configured default without changing global model settings.

Copy the capability profile example:

```bash
mkdir -p work
cp assets/backend-capabilities.example.json work/capabilities.json
.venv/bin/python scripts/check_environment.py --mode extract \
  --backend opencode --capabilities work/capabilities.json
```

Fill in image count, edge length, or pixel limits based on the provider's actual limits. Leave unknown limits unset. If the profile specifies a `model`, pass the same identifier with `--model provider/model`. Executing workers with `--execute` requires a valid capability profile; offline preparation may omit it.

## Quick start

Generate a self-authored English sample page without downloading a book or calling a model:

```bash
.venv/bin/python examples/make_sample.py --output work/source
.venv/bin/python scripts/prepare_images.py --pages-dir work/source --output work/prepared --pages 1 --max-pixels 2000000
```

Then send this request to the coordinator agent:

> Use $pdf-extract to extract page 1 from work/source in this project. Run scripts with .venv/bin/python. Use the configured DeepSeek V4.1 Flash worker through OpenCode with work/capabilities.json. Verify image access first, and retain original outputs, provenance, and uncertainties. Extract only; do not generate LaTeX for this run.

To run the single-page worker manually:

```bash
.venv/bin/python scripts/run_page_jobs.py \
  --prepared-dir work/prepared --source work/source --expected-pages 1 \
  --mode extract --backend opencode --capabilities work/capabilities.json \
  --workers 1 --max-invocations 1 --output work/jobs --execute
```

The last command calls the configured model and may incur charges. Remove `--execute` to prepare tasks only. Raw worker outputs are saved under `work/jobs`; the coordinator then handles proofreading, normalization, and assembly. See [OpenCode invocation](references/opencode.md) for concurrency, retries, and token accounting.

## Optional features

- **LaTeX/PDF:** Install XeLaTeX, ctex, the required packages, and fonts separately; TeX Live is recommended. Poppler can render previews. The default body fonts use Noto Serif/Sans and Noto CJK families; explicit Kai styles also require an available Kai font. Fonts, templates, and compilation engines are configurable. Source packages omit font files by default. See [LaTeX export](references/latex-export.md) and [template selection](references/template-selection.md).
- **Figure reconstruction:** `.venv/bin/python -m pip install -e '.[figures]'`.
- **Local OCR:** `.venv/bin/python -m pip install -e '.[ocr]'`. This optional route may require large model downloads and a GPU. Direct visual transcription does not require it.
- **Image rectification:** `.venv/bin/python -m pip install -e '.[rectify]'`.

Use `extract` for extraction, `proofread` for source-image proofreading, and `typeset` for template preflight, compilation, and visual acceptance as well. A reflowed PDF may have a different page count from the source, and successful compilation does not establish content accuracy. [SKILL.md](SKILL.md) routes to the detailed workflow references as needed.

## Development and distribution

```bash
.venv/bin/python -m pip install -e '.[dev,figures]'
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/package_skill.py --output work/pdf-extract.zip
```

Tests do not call real paid models. Compilation tests are skipped when TeX is unavailable. Full typesetting checks also need TeX Live, Poppler, and the selected fonts. CI runs local tests and packaging checks; it does not validate remote model providers.

`input/`, `output/`, `runs/`, `_stash/`, and `work/` are local workspaces excluded from the public repository. Public tests use self-authored fixtures. Distribution ZIPs contain the skill's runtime resources, installation instructions, and licenses, excluding private inputs, run records, and font files.

## License

Original project content is released under [MIT](LICENSE). Third-party components retain their own licenses: PyMuPDF/MuPDF is available under AGPL or a commercial license, and ElegantBook uses LPPL. See [third-party notices](THIRD_PARTY_NOTICES.md).
