#!/usr/bin/env python3
"""Prepare one isolated page job; invoke OpenCode only with --execute."""
import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from backend_capabilities import load_capabilities, check_images
from runtime_contract import stage_identity
from validate_outputs import validate
from semantic_contract import STYLES
from prepare_images import manifest_errors, select_pages
from opencode_runtime import invocation, safe_diagnostic, session_metadata


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--expected-pages', help='Optional complete source scope assertion, e.g. 1-50')
    parser.add_argument('--job-dir', required=True, type=Path, help='New isolated directory')
    parser.add_argument('--model', help='Optional provider/model; omitted uses the configured OpenCode default')
    parser.add_argument('--capabilities', type=Path, help='Declared image/tool limits and token semantics')
    parser.add_argument('--backend', choices=['opencode','codex'], default='opencode', help='Preparation identity; standalone execution supports OpenCode only')
    parser.add_argument('--draft', type=Path, help='OCR Markdown to proofread against source images')
    parser.add_argument('--draft-json', type=Path, help='Optional OCR blocks/coordinates')
    parser.add_argument('--disable-mcp', action='append', default=[], help='Configured server name; repeat as needed')
    parser.add_argument('--model-options', type=Path, help='JSON object of reasoning/provider options only')
    parser.add_argument('--steps', type=int, default=24)
    parser.add_argument('--timeout', type=float, default=900)
    parser.add_argument('--opencode', default='opencode', help='CLI executable')
    parser.add_argument('--execute', action='store_true', help='Perform an authorized model call')
    parser.add_argument('--json-only', action='store_true', help='Only ask the model for canonical JSON')
    parser.add_argument('--prompt-template', type=Path, help='Explicit frozen template for all pages in a run')
    args = parser.parse_args()
    if args.steps < 1 or args.timeout <= 0 or (args.backend == 'opencode' and args.model is not None and '/' not in args.model):
        parser.error('Use positive steps/timeout and a provider/model identifier')
    if args.draft_json and not args.draft:
        parser.error('draft-json requires draft')
    manifest_path = args.manifest.expanduser().resolve(strict=True)
    manifest = json.loads(manifest_path.read_text())
    readiness_errors = manifest_errors(manifest, select_pages(args.expected_pages) if args.expected_pages else None)
    if readiness_errors:
        parser.error('; '.join(readiness_errors))
    matches = [p for p in manifest['pages'] if p['page'] == args.page]
    if len(matches) != 1:
        parser.error('Manifest must contain the requested physical page exactly once')
    root = manifest_path.parent
    page = json.loads(json.dumps(matches[0]))
    images = [page['original']] + page['images']
    verified_paths = []
    for record in images:
        path = (root / record['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or digest(path) != record['sha256']:
            parser.error('Source image missing, outside work directory, or hash changed')
        verified_paths.append(path)
    try:
        capabilities = load_capabilities(args.capabilities, backend=args.backend, model=args.model, required=args.execute)
    except ValueError as error:
        parser.error(str(error))
    budget = check_images(capabilities, verified_paths[1:], verified_paths[:1])
    options = {}
    if args.model_options and not args.model:
        parser.error('model-options requires an explicit model identity')
    if args.model_options:
        options = json.loads(args.model_options.read_text())
        if not isinstance(options, dict) or set(options) - {'reasoning', 'provider'}:
            parser.error('model-options accepts only reasoning/provider; no credentials or baseURL')
        permitted = {'reasoning': {'effort', 'enabled', 'exclude', 'max_tokens'},
                     'provider': {'order', 'only', 'ignore', 'allow_fallbacks', 'require_parameters',
                                  'data_collection', 'sort', 'quantizations', 'max_price'}}
        for key, value in options.items():
            if not isinstance(value, dict) or set(value) - permitted[key]:
                parser.error(f'Unsupported {key} options; keep credentials out of job configuration')
    job = args.job_dir.expanduser().resolve()
    if job.exists():
        parser.error('Job directory already exists; create a new attempt rather than overwriting')
    (job / 'input' / f'{args.page:04d}').mkdir(parents=True)
    (job / 'output').mkdir()
    for record, source in zip(images, verified_paths):
        new_path = Path(f'{args.page:04d}') / source.name
        target = job / 'input' / new_path
        if target.exists():
            parser.error('Image basenames collide; normalize source filenames before preparing job')
        shutil.copyfile(source, target)
        record['path'] = new_path.as_posix()
    if args.draft:
        shutil.copyfile(args.draft.expanduser().resolve(strict=True), job / 'input/draft.md')
    if args.draft_json:
        shutil.copyfile(args.draft_json.expanduser().resolve(strict=True), job / 'input/draft.json')
    single_manifest = {key: value for key, value in manifest.items() if key != 'pages'}
    single_manifest['pages'] = [page]
    single_manifest['source_requested_pages'] = manifest.get('source_requested_pages',
        manifest.get('requested_pages', [p['page'] for p in manifest['pages']]))
    single_manifest.update(requested_pages=[args.page], completed_pages=[args.page],
                           preparation_status='complete')
    (job / 'input/manifest.json').write_text(json.dumps(single_manifest, ensure_ascii=False, indent=2))
    template = args.prompt_template or Path(__file__).resolve().parent.parent / 'assets/page-prompt.txt'
    prompt = template.read_text()
    substitutions = {'mode': 'Proofread the OCR draft against the source images' if args.draft else 'Independent visual OCR',
                     'page': str(args.page), 'page_id': f'{args.page:04d}',
                     'markdown_instruction': '' if args.json_only else f'output/{args.page:04d}.md: reading edition; use single dollar signs for inline formulas and double dollar signs for display formulas; preserve correct local styles.',
                     'style_kinds': '/'.join(sorted(STYLES)),
                     'draft_note': ('The draft is in input/draft.md; verify each passage against the source images, and changes records the actual edits.'
                                    + ('Original layout information is in input/draft.json; read only the relevant ranges when coordinate/font context is needed, no need to re-read the whole file.' if args.draft_json else ''))
                                   if args.draft else 'Do not read other candidates\' results or answers.'}
    for key, value in substitutions.items():
        prompt = prompt.replace('{{' + key + '}}', value)
    (job / 'prompt.txt').write_text(prompt)
    def paths(folder):
        target = job / folder
        forms = {str(target), folder}
        # OpenCode normalizes tool paths against its worktree. Account for
        # both a parent Git checkout and '/' in a standalone directory.
        forms.update(str(target.relative_to(parent)) for parent in job.parents)
        return {pattern: 'allow' for form in forms for pattern in (form, form + '/*')}
    permission = {'*': 'deny',
                  'read': {'*': 'deny', **paths('input'), **paths('output')},
                  'edit': {'*': 'deny', **paths('output')},
                  'external_directory': 'deny'}
    config = {'$schema': 'https://opencode.ai/config.json', 'model': args.model,
              'permission': permission,
              'mcp': {name: {'enabled': False} for name in args.disable_mcp},
              'agent': {'pdf-ocr': {'description': 'Faithful source-grounded optical transcription',
                                   'mode': 'primary', 'model': args.model, 'steps': args.steps,
                                   'permission': permission,
                                   'prompt': 'Read source pixels faithfully and write the assigned files. '
                                             'Preserve uncertainty and source errors. Never summarize, '
                                             'invent textbook content, or solve mathematical problems.'}}}
    if args.model is None:
        config.pop('model', None)
        config['agent']['pdf-ocr'].pop('model', None)
    if options:
        provider, model = args.model.split('/', 1)
        config['provider'] = {provider: {'models': {model: {'options': options}}}}
    (job / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))
    record = {'page': args.page, 'model': args.model, 'mode': substitutions['mode'],
              'status': 'prepared', 'paid_call_started': False, 'attempts': 0,
              'attachments_submitted': page['images'],
              'image_delivery': 'not_verified; CLI arguments alone do not prove server-side input',
              'mcp_disabled': args.disable_mcp, 'timeout_seconds': args.timeout}
    (job / 'run.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    record.update(capabilities=capabilities, image_budget=budget, stage_runtime=stage_identity([Path(__file__)]))
    (job / 'run.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    if not args.execute:
        print(json.dumps({'status': 'prepared', 'job': str(job), 'paid_call_started': False,
                          'attachments': len(page['images'])}))
        return
    if args.backend != 'opencode':
        parser.error('Standalone execution supports OpenCode; use run_page_jobs.py for Codex')
    command, environment, runtime = invocation(args.opencode, job, config, prompt,
        [job / 'input' / image['path'] for image in page['images']], 'pdf-ocr', f'PDF page {args.page:04d}')
    record['runtime'] = runtime
    started = time.perf_counter()
    record.update(status='running', paid_call_started=True, attempts=1, started_unix=time.time())
    (job / 'run.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    try:
        process = subprocess.Popen(command, cwd=job, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as exc:
        record.update(status='failed_to_launch', paid_call_started=False,
                      error_type=type(exc).__name__, wall_seconds=time.perf_counter()-started)
        (job / 'run.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
    sessions, usages, error_types = set(), [], []
    # Retain only known metadata; no response text, image data, auth, or raw transport logs.
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        session = event.get('sessionID')
        if isinstance(session, str):
            sessions.add(session)
        if event.get('type') == 'step_finish' and isinstance(event.get('part'), dict):
            part = event['part']
            tokens = part.get('tokens', {})
            if isinstance(tokens, dict):
                cache = tokens.get('cache', {})
                cache = cache if isinstance(cache, dict) else {}
                selected = {k: tokens[k] for k in ('input', 'output', 'reasoning', 'total')
                            if type(tokens.get(k)) in (int, float)}
                selected['cache'] = {k: cache[k] for k in ('read', 'write')
                                     if type(cache.get(k)) in (int, float)}
                usages.append({'part_id': part.get('id'), 'tokens_as_returned': selected,
                               'opencode_catalogue_cost': part.get('cost')
                               if type(part.get('cost')) in (int, float) else None})
        if event.get('type') == 'error':
            error = event.get('error', {})
            if isinstance(error, dict) and isinstance(error.get('name'), str):
                error_types.append(error['name'])
    record.update(session_metadata(args.opencode, job, sorted(sessions), runtime))
    report = validate(job / 'input/manifest.json', job / 'output', require_markdown=not args.json_only)
    (job / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    record.update(exit_code=process.returncode, timed_out=timed_out,
                  wall_seconds=time.perf_counter() - started, ended_unix=time.time(),
                  sessions=sorted(sessions), returned_usage=usages, error_types=error_types,
                  diagnostic_types=safe_diagnostic(stderr),
                  usage_partial=timed_out or process.returncode != 0 or not usages,
                  usage_completeness='not_reconciled_with_provider; step_finish metadata only',
                  status='validated' if not timed_out and process.returncode == 0 and report['structure_ok'] else 'needs_review')
    (job / 'run.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({key: record[key] for key in ('page', 'status', 'exit_code', 'wall_seconds', 'timed_out')}))
    raise SystemExit(0 if record['status'] == 'validated' else 1)


if __name__ == '__main__':
    main()
