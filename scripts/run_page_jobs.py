#!/usr/bin/env python3
"""Bounded, resumable page transcription; model calls require --execute.

The image producer publishes page-NNNN.json beside manifest.json. Only the
terminal full manifest proves preparation completion. OCR producers may run a
separate persistent single-GPU queue; drafts are consumed without occupying
vision workers. Structural validation is never an accuracy endorsement.
"""
import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from prepare_images import atomic_json, digest, manifest_errors, select_pages
from validate_outputs import validate
from format_preflight import load_format_profile, check_template_ready, validate_preflight_scope
from source_identity import source_digest, manifest_source_hash
from backend_capabilities import load_capabilities
from runtime_contract import stage_identity
from reuse_artifacts import adopt_page
from opencode_runtime import invocation, safe_diagnostic, runtime_identity, session_metadata

SCRIPTS = Path(__file__).resolve().parent


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def files_under(path):
    return {str(p.relative_to(path)): digest(p) for p in sorted(path.rglob('*')) if p.is_file()}


def verify_manifest(path, source_hash, expected, page=None):
    data = load(path)
    errors = manifest_errors(data, expected if page is None else [page])
    if manifest_source_hash(data) != source_hash:
        errors.append('Source PDF hash differs from current source file')
    scope = data.get('source_requested_pages', data.get('requested_pages'))
    if scope != expected:
        errors.append('Full source page scope differs from --expected-pages')
    if data.get('preparation_status') != 'complete':
        errors.append('Only terminal complete snapshots are consumable')
    root = path.parent.resolve()
    for record in data.get('pages', []):
        for image in [record['original']] + record['images']:
            candidate = (root / image['path']).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file() or digest(candidate) != image['sha256']:
                errors.append('Source image missing/outside root/hash changed: ' + image['path'])
    if errors:
        raise ValueError('; '.join(errors))
    return data


def bounded_process(command, cwd, timeout, *, prompt=None, env=None):
    started = time.perf_counter()
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE if prompt else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        for sig, grace in [(signal.SIGTERM, 5), (signal.SIGKILL, 5)]:
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = proc.communicate(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            raise RuntimeError('Model process group did not terminate')
    return proc.returncode, timed_out, stdout, time.perf_counter() - started, safe_diagnostic(stderr)


def usage_metadata(stdout, backend):
    """Retain known metadata only, never raw transport, credentials or response text."""
    usage, errors, sessions = [], [], set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        sid = event.get('sessionID', event.get('thread_id'))
        if isinstance(sid, str):
            sessions.add(sid)
        if backend == 'codex' and event.get('type') == 'turn.completed':
            counts = event.get('usage', {})
            if isinstance(counts, dict):
                usage.append({k: counts[k] for k in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
                              if type(counts.get(k)) is int})
        if backend == 'opencode' and event.get('type') == 'step_finish':
            part = event.get('part', {})
            counts = part.get('tokens', {}) if isinstance(part, dict) else {}
            if isinstance(counts, dict):
                selected = {k: counts[k] for k in ('input', 'output', 'reasoning', 'total')
                            if type(counts.get(k)) in (int, float)}
                cache = counts.get('cache', {})
                selected['cache'] = {k: cache[k] for k in ('read', 'write')
                                     if isinstance(cache, dict) and type(cache.get(k)) in (int, float)}
                usage.append({'part_id': part.get('id'), 'tokens_as_returned': selected})
        if event.get('type') in ('error', 'turn.failed'):
            error = event.get('error', {})
            errors.append(error.get('name', 'backend_error') if isinstance(error, dict) else 'backend_error')
    return {'sessions': sorted(sessions), 'returned_usage': usage, 'error_types': errors,
            'usage_partial': not bool(usage), 'actual_cost': None,
            'image_delivery': 'CLI attachment paths and hashes only; server pixels not independently audited'}


def resume_ok(job, fingerprint):
    checkpoint = job / 'checkpoint.json'
    if not checkpoint.is_file():
        return False
    record = load(checkpoint)
    if record.get('fingerprint') != fingerprint or record.get('status') not in ('validated', 'prepared'):
        return False
    hashes = record.get('file_hashes', {})
    if not hashes or files_under(job) != {**hashes, 'checkpoint.json': digest(checkpoint)}:
        return False
    if record['status'] == 'validated':
        return validate(job / 'input/manifest.json', job / 'output', require_markdown=not record.get('json_only', False))['structure_ok']
    return True


def verify_draft(draft, page_record, source_hash, contract):
    ready_path = draft.parent / 'page-ready.json'
    if not ready_path.is_file():
        if (draft.parent / 'error.txt').is_file():
            raise ValueError(f'GPU OCR producer failed on page {page_record["page"]}; no implicit vision fallback')
        return False
    ready = load(ready_path)
    if (ready.get('page') != page_record['page'] or ready.get('status') != 'produced'
            or ready.get('source_sha256') != source_hash
            or ready.get('image_sha256') != page_record['original']['sha256']
            or ready.get('contract') != contract):
        raise ValueError('OCR readiness source/page/image/model/config contract mismatch')
    outputs = ready.get('outputs', {})
    if not isinstance(outputs, dict) or not {'original.md', 'raw-blocks.json'} <= set(outputs):
        raise ValueError('OCR readiness must declare original.md and raw-blocks.json hashes')
    for name, expected_hash in outputs.items():
        path = (draft.parent / name).resolve()
        if not path.is_relative_to(draft.parent) or not path.is_file() or digest(path) != expected_hash:
            raise ValueError('OCR output missing/outside page root/hash changed')
    return True


def run_page(args, page, manifest_path, fingerprint, number, draft):
    job = args.output / 'pages' / f'{page:04d}' / f'attempt-{number:03d}'
    started = time.perf_counter()
    prep = [sys.executable, str(SCRIPTS / 'opencode_job.py'), '--manifest', str(manifest_path),
            '--page', str(page), '--job-dir', str(job),
            '--steps', str(args.steps), '--timeout', str(args.timeout), '--backend', args.backend]
    if args.capabilities:
        prep.extend(['--capabilities', str(args.capabilities)])
    if args.model:
        prep.extend(['--model', args.model if args.backend == 'codex' or '/' in args.model else 'codex/' + args.model])
    prep.extend(['--prompt-template', str(args.prompt_snapshot)])
    if not args.with_markdown:
        prep.append('--json-only')
    for server in args.disable_mcp:
        prep.extend(['--disable-mcp', server])
    if args.model_options:
        prep.extend(['--model-options', str(args.model_options)])
    if draft:
        prep.extend(['--draft', str(draft)])
        blocks = sorted(draft.parent.glob('*-blocks.json'))
        if blocks:
            prep.extend(['--draft-json', str(blocks[0])])
    record = {'page': page, 'attempt': number, 'fingerprint': fingerprint,
              'started_unix': time.time(), 'backend': args.backend, 'model': args.model,
              'status': 'preparing', 'model_call_started': False, 'model_invocations': 0,
              'json_only': not args.with_markdown}
    try:
        prepared = subprocess.run(prep, capture_output=True, text=True, timeout=60)
        if prepared.returncode:
            raise ValueError('opencode_job input/prompt/config preparation failed; inspect input contracts')
        record['preparation_seconds'] = time.perf_counter()-started
        atomic_json(job / 'checkpoint.json', record)
        if not args.execute:
            record.update(status='prepared')
        else:
            config = load(job / 'config.json')
            prompt = (job / 'prompt.txt').read_text()
            prompt += ('\nProcess only the input/ content of the assigned pages, and write only to output/. Do not explore the project,'
                       'read historical answers, access the network, or start subagents; the shared context is already provided in this job.'
                       'Do not complete cross-page truncated sentences; for body blocks set evidence only to unreviewed or uncertain;'
                       'a model self-report does not count as independent source_checked.\n')
            (job / 'prompt.txt').write_text(prompt)
            page_manifest = load(job / 'input/manifest.json')['pages'][0]
            attachments = [job / 'input' / image['path'] for image in page_manifest['images']]
            env = os.environ.copy()
            if args.backend == 'codex':
                command = [args.codex, 'exec', '--ignore-user-config', '--skip-git-repo-check',
                           '--ephemeral', '-m', args.model, '-s', 'workspace-write', '-C', str(job), '--json']
                for image in attachments:
                    command.extend(['-i', str(image)])
                command.append('-')
                stdin = prompt
            else:
                command, env, runtime = invocation(args.opencode, job, config, prompt, attachments,
                                                   'pdf-ocr', f'PDF page {page:04d}')
                record['runtime'] = runtime
                stdin = None
            record.update(status='running', model_call_started=True, model_invocations=1,
                          attachments_submitted=[{'path': str(p), 'sha256': digest(p)} for p in attachments])
            atomic_json(job / 'checkpoint.json', record)
            atomic_json(job/'run.json', {**load(job/'run.json'), **record,
                        'record_role': 'execution', 'paid_call_started': True, 'attempts': 1})
            try:
                code, timed_out, stdout, seconds, diagnostics = bounded_process(command, job, args.timeout, prompt=stdin, env=env)
            except OSError:
                record.update(model_call_started=False, model_invocations=0)
                raise
            report = validate(job / 'input/manifest.json', job / 'output', require_markdown=args.with_markdown)
            atomic_json(job / 'validation.json', report)
            record.update(usage_metadata(stdout, args.backend))
            if args.backend == 'opencode':
                record.update(session_metadata(args.opencode, job, record['sessions'], runtime))
            record.update(exit_code=code, timed_out=timed_out, model_seconds=seconds, diagnostic_types=diagnostics,
                          status='validated' if code == 0 and not timed_out and report['structure_ok'] else 'needs_review')
            record['usage_partial'] |= timed_out or code != 0
    except Exception as exc:
        record.update(status='failed', error_type=type(exc).__name__, error=str(exc))
    record.update(wall_seconds=time.perf_counter()-started, ended_unix=time.time())
    if job.is_dir():
        prepared_metadata = load(job/'run.json') if (job/'run.json').is_file() else {}
        record['capabilities'] = prepared_metadata.get('capabilities')
        record['image_budget'] = prepared_metadata.get('image_budget')
        # opencode_job only prepares the files for the coordinator. Its initial
        # run.json must not continue claiming that no call took place.
        atomic_json(job/'run.json', {**prepared_metadata, **record,
                    'record_role': 'execution', 'paid_call_started': record['model_call_started'],
                    'attempts': record['model_invocations']})
        record['file_hashes'] = {k: v for k, v in files_under(job).items() if k != 'checkpoint.json'}
        atomic_json(job / 'checkpoint.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-dir', type=Path, required=True)
    parser.add_argument('--expected-pages', required=True, help='Whole producer scope, e.g. 1-50')
    parser.add_argument('--pages', help='Mutually exclusive owned subset; default whole scope')
    parser.add_argument('--source', '--source-pdf', dest='source_pdf', type=Path, required=True, help='Current original source; hash checked again at completion')
    parser.add_argument('--output', type=Path, required=True, help='Coordinator-owned new directory, or --resume')
    parser.add_argument('--backend', choices=['opencode', 'codex'], default='opencode')
    parser.add_argument('--model', help='Optional for OpenCode; uses user-configured default when omitted')
    parser.add_argument('--mode', choices=['extract', 'proofread', 'typeset'], default='extract', help='Extraction/proofreading do not require a LaTeX template')
    parser.add_argument('--capabilities', type=Path, help='Provider-neutral declared image/tool limits and token semantics')
    parser.add_argument('--reuse-validated-from', type=Path, action='append', default=[], help='Explicitly reuse source/image/schema-validated pages from an older run')
    parser.add_argument('--reuse-only', action='store_true', help='Require reusable results for all pages; no backend executable or model call needed')
    parser.add_argument('--max-invocations', type=int, help='Hard cap on CLI invocations across all attempts, including failed ones')
    parser.add_argument('--workers', '--max-workers', type=int, default=2)
    parser.add_argument('--drafts-dir', '--draft-dir', type=Path, help='Separate GPU queue writes NNNN/original.md atomically')
    parser.add_argument('--no-preflight', action='store_true', help='Skip first real-page validation gate; only after a separate verified preflight')
    parser.add_argument('--producer-timeout', type=float, default=1800, help='Maximum continuous wait with no producer progress; active model processing does not count')
    parser.add_argument('--timeout', type=float, default=900, help='Per-model-job process timeout')
    parser.add_argument('--poll-seconds', type=float, default=1)
    parser.add_argument('--steps', type=int, default=24)
    parser.add_argument('--opencode', default='opencode')
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--disable-mcp', action='append', default=[])
    parser.add_argument('--model-options', type=Path)
    parser.add_argument('--prompt-template', type=Path, help='Task-specific page prompt; frozen and verified on resume')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--retry-pages', help='Explicitly allow ONE new attempt on these failed pages; no automatic retries')
    parser.add_argument('--format-profile', type=Path, help='Current source-bound format/book-format.json')
    parser.add_argument('--template-ready', type=Path, help='Artifact-bound accepted template record for production')
    parser.add_argument('--preflight-mode', action='store_true', help='Non-production sample mode; at most 8 owned pages, profile still required for execution')
    parser.add_argument('--execute', action='store_true', help='Start authorized model calls; otherwise preparation only')
    parser.add_argument('--with-markdown', action='store_true', help='Also ask the model for a redundant per-page Markdown derivative')
    args = parser.parse_args()
    expected = select_pages(args.expected_pages)
    owned = select_pages(args.pages) if args.pages else expected
    retries = set(select_pages(args.retry_pages)) if args.retry_pages else set()
    if not set(owned) <= set(expected) or not retries <= set(owned):
        parser.error('Owned/retry pages must be inside the complete expected scope')
    if args.workers < 1 or min(args.timeout, args.producer_timeout, args.poll_seconds, args.steps) <= 0:
        parser.error('Worker count and timeout/step values must be positive')
    for key in ('prepared_dir', 'source_pdf', 'output', 'drafts_dir', 'model_options', 'format_profile', 'template_ready', 'capabilities'):
        value = getattr(args, key)
        if value:
            setattr(args, key, value.expanduser().resolve())
    if args.reuse_validated_from and not (args.execute or args.reuse_only):
        parser.error('Artifact reuse requires reuse-only or execute for remaining pages')
    if args.reuse_only and (args.execute or not args.reuse_validated_from):
        parser.error('reuse-only requires reuse-validated-from and excludes execute')
    if args.mode == 'proofread' and not args.drafts_dir and not args.reuse_only:
        parser.error('proofread mode requires drafts-dir; use extract for independent transcription')
    if args.backend == 'codex' and not args.model and not args.reuse_only:
        parser.error('Codex requires an explicit model')
    if args.max_invocations is not None and args.max_invocations < 1:
        parser.error('max-invocations must be positive')
    if args.backend == 'opencode' and args.model is not None and '/' not in args.model:
        parser.error('OpenCode model must be provider/model')
    if args.model_options and not args.model:
        parser.error('model-options requires an explicit model identity')
    if args.backend == 'codex' and args.model_options:
        parser.error('model-options is OpenCode-only; do not silently ignore Codex options')
    source_hash = source_digest(args.source_pdf)
    format_metadata = {'status': 'not_checked', 'production': False}
    try:
        if args.preflight_mode:
            validate_preflight_scope(owned)
        if args.format_profile:
            profile = load_format_profile(args.format_profile, source_hash)
            format_metadata.update(status='profile_validated', profile_path=str(args.format_profile),
                                   profile_sha256=profile['profile_sha256'], template_dir=profile['template_dir'])
        elif args.execute and args.mode == 'typeset':
            raise ValueError('--execute requires --format-profile even in preflight mode')
        if args.template_ready and args.format_profile:
            format_metadata.update(check_template_ready(args.format_profile, args.template_ready, source_hash))
            format_metadata['production'] = not args.preflight_mode
        elif args.execute and args.mode == 'typeset' and not args.preflight_mode:
            raise ValueError('Production --execute requires both --format-profile and --template-ready')
        elif args.template_ready:
            raise ValueError('--template-ready requires --format-profile')
    except ValueError as exc:
        if args.execute:
            parser.error(str(exc))
        format_metadata.update(status='needs_review', diagnostic=str(exc))
    if args.mode != 'typeset' and not args.format_profile:
        format_metadata.update(status='not_required', production=False, diagnostic='Extraction/proofreading uses the source/data contract; template admission is deferred to typesetting')
    if not args.execute and format_metadata['status'] != 'accepted' and 'diagnostic' not in format_metadata:
        format_metadata['diagnostic'] = 'Preparation only: production requires a valid format profile and artifact-bound template-ready'
    prompt_path = (args.prompt_template.expanduser().resolve(strict=True) if args.prompt_template
                   else SCRIPTS.parent / 'assets/page-prompt.txt')
    prompt = prompt_path.read_text()
    for required in ('{{page}}', '{{page_id}}', 'blocks', 'viewed_images', 'uncertainties', 'changes'):
        if required not in prompt:
            parser.error('Unified page prompt missing required schema/substitution: ' + required)
    executable = args.codex if args.backend == 'codex' else args.opencode
    executable_path = shutil.which(executable)
    if args.execute and not executable_path:
        parser.error('Backend executable unavailable')
    ocr_contract = None
    if args.drafts_dir:
        ocr_contract_path = args.drafts_dir / 'contract.json'
        if not ocr_contract_path.is_file():
            parser.error('Start the OCR producer first; --drafts-dir requires its source/model/config contract.json')
        ocr_contract = load(ocr_contract_path)
        if ocr_contract.get('source_sha256') != source_hash:
            parser.error('OCR producer source hash differs from current PDF')
        # Frozen producer provenance is verified against every ready receipt.
        # Consuming saved artifacts must not require reinstalling the producer.
        if not isinstance(ocr_contract.get('kind', 'legacy_mineru'), str):
            parser.error('Invalid draft producer kind')
    try:
        capabilities = load_capabilities(args.capabilities, backend=args.backend, model=args.model, required=args.execute)
    except ValueError as error:
        parser.error(str(error))
    options = load(args.model_options) if args.model_options else {}
    permitted = {'reasoning': {'effort', 'enabled', 'exclude', 'max_tokens'},
                 'provider': {'order', 'only', 'ignore', 'allow_fallbacks', 'require_parameters',
                              'data_collection', 'sort', 'quantizations', 'max_price'}}
    if (not isinstance(options, dict) or set(options)-set(permitted)
            or any(not isinstance(value, dict) or set(value)-permitted[key] for key, value in options.items())):
        parser.error('model-options accepts only approved reasoning/provider keys; never credentials or baseURL')
    contract = {'version': 3, 'source_pdf_sha256': source_hash, 'expected_pages': expected,
                'owned_pages': owned, 'backend': args.backend, 'model': args.model, 'mode': args.mode,
                'backend_executable': executable_path or executable,
                'backend_executable_sha256': digest(Path(executable_path)) if executable_path else None,
                'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'base_prompt_sha256': digest(prompt_path), 'json_only': not args.with_markdown,
                'steps': args.steps, 'timeout': args.timeout,
                'first_page_preflight': not args.no_preflight,
                'format_admission': format_metadata, 'preflight_mode': args.preflight_mode,
                'format_profile_sha256': digest(args.format_profile) if args.format_profile and args.format_profile.is_file() else None,
                'template_ready_sha256': digest(args.template_ready) if args.template_ready and args.template_ready.is_file() else None,
                'model_options': options,
                'opencode_runtime': runtime_identity(executable, next(p for p in args.output.parents if p.is_dir())) if args.execute and args.backend == 'opencode' else None,
                'disabled_mcp': sorted(args.disable_mcp), 'drafts_dir': str(args.drafts_dir) if args.drafts_dir else None,
                'ocr_contract': ocr_contract,
                'capabilities': capabilities,
                'reuse_only': args.reuse_only,
                'reuse_sources': {str(p.resolve()): digest(p.resolve()/'contract.json') for p in args.reuse_validated_from},
                'stage_runtime': stage_identity([Path(__file__), SCRIPTS/'opencode_job.py'])}
    if args.output.exists() and not args.resume:
        parser.error('Output exists; use --resume only with identical source/model/config')
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (args.output / '.coordinator.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('Another coordinator owns this output directory')
    contract_path = args.output / 'contract.json'
    if contract_path.exists():
        if load(contract_path) != contract:
            parser.error('Resume source/model/prompt/tool/config differs; start a new run directory')
    else:
        if args.resume:
            parser.error('Resume requires an existing verifiable contract')
        atomic_json(contract_path, contract)
    args.prompt_snapshot = args.output / 'prompt-template.txt'
    if args.prompt_snapshot.exists():
        if digest(args.prompt_snapshot) != contract['prompt_sha256']:
            parser.error('Frozen run prompt has changed')
    else:
        args.prompt_snapshot.write_text(prompt)
    started = time.perf_counter()
    run_started_unix = time.time()
    pending, active, records, fingerprints = set(owned), {}, {}, {}
    image_manifest = args.prepared_dir / 'manifest.json'
    final_data = None
    fatal = None
    preflight_passed = not args.execute or args.no_preflight
    producer_wait_started = None
    producer_signature = None
    new_invocations = 0
    historical_invocations = sum(load(p).get('model_invocations', 0) for p in (args.output / 'pages').glob('*/attempt-*/checkpoint.json'))

    def publish(status):
        all_records = [records[p] for p in sorted(records)]
        historical = []
        for checkpoint in (args.output / 'pages').glob('*/attempt-*/checkpoint.json'):
            historical.append(load(checkpoint))
        summary = {'status': status, 'source_pdf_sha256': source_hash,
                   'expected_pages': expected, 'owned_pages': owned,
                   'prepared_pages': [r['page'] for r in all_records if r['status'] == 'prepared'],
                   'validated_pages': [r['page'] for r in all_records if r['status'] == 'validated'],
                   'failed_pages': [r['page'] for r in all_records if r['status'] not in ('validated', 'prepared')],
                   'active_pages': sorted(active.values()), 'pending_pages': sorted(pending),
                   'workers': args.workers, 'mode': args.mode, 'started_unix': run_started_unix,
                   'max_invocations': args.max_invocations,
                   'first_page_preflight_passed': preflight_passed,
                   'preflight_mode': args.preflight_mode, 'production': args.execute and format_metadata.get('production', False),
                   'format_admission': format_metadata,
                   'wall_seconds': time.perf_counter()-started,
                   'model_invocations': sum(r.get('model_invocations', 0) for r in historical),
                   'model_request_count': 'unknown; CLI invocation may include multiple requests',
                   'model_seconds_sum': sum(r.get('model_seconds', 0) for r in historical),
                   'reused_pages': [r['page'] for r in all_records if r.get('reused')],
                   'attempt_records': historical, 'error': fatal,
                   'quality': 'structure/hash/self-reported coverage only; source accuracy requires independent QA'}
        atomic_json(args.output / 'progress.json', summary)
        return summary

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        while pending or active or final_data is None:
            try:
                if final_data is None and image_manifest.is_file():
                    final_data = verify_manifest(image_manifest, source_hash, expected)
                producer_progress = args.prepared_dir / 'progress.json'
                if producer_progress.is_file() and load(producer_progress).get('preparation_status') == 'failed':
                    raise ValueError('Image producer reported failure; ready subset is not complete')
                for future in list(active):
                    if not future.done():
                        continue
                    page = active.pop(future)
                    records[page] = future.result()
                    if not preflight_passed:
                        if records[page]['status'] != 'validated':
                            raise ValueError(f'First real page {page} failed validation; expanding worker count is blocked')
                        preflight_passed = True
                for page in sorted(pending):
                    limit = args.workers if preflight_passed else 1
                    if len(active) >= limit:
                        break
                    manifest_path = args.prepared_dir / f'page-{page:04d}.json'
                    if manifest_path.is_file():
                        data = verify_manifest(manifest_path, source_hash, expected, page)
                    elif final_data is not None:
                        manifest_path, data = image_manifest, final_data
                    else:
                        continue
                    page_record = next(p for p in data['pages'] if p['page'] == page)
                    if args.reuse_validated_from:
                        reuse_fingerprint = canonical_hash({'contract': contract, 'page': page_record, 'reuse': True})
                        destination = args.output/'pages'/f'{page:04d}'/'attempt-001'
                        reused = None
                        if destination.exists():
                            if not resume_ok(destination, reuse_fingerprint):
                                raise ValueError('Existing reused artifact changed')
                            reused = load(destination/'checkpoint.json')
                        else:
                            reused = adopt_page(args.reuse_validated_from, destination, page, page_record,
                                                source_hash, reuse_fingerprint)
                        if reused:
                            records[page] = reused
                            # A reused artifact does not pilot a different
                            # backend/model for the pages still to execute.
                            pending.remove(page)
                            continue
                        if args.reuse_only:
                            raise ValueError(f'No validated reusable result for page {page}')
                    draft = args.drafts_dir / f'{page:04d}/original.md' if args.drafts_dir else None
                    if draft is not None and not verify_draft(draft, page_record, source_hash, ocr_contract):
                        continue
                    blocks = sorted(draft.parent.glob('*-blocks.json')) if draft else []
                    fingerprint = canonical_hash({'contract': contract, 'page': page_record,
                                                  'source': data['source'], 'draft_hash': digest(draft) if draft else None,
                                                  'draft_ready_hash': digest(draft.parent/'page-ready.json') if draft else None,
                                                  'draft_blocks_hash': digest(blocks[0]) if blocks else None})
                    fingerprints[page] = fingerprint
                    attempts = sorted((args.output / 'pages' / f'{page:04d}').glob('attempt-*'))
                    if attempts:
                        previous = attempts[-1]
                        if resume_ok(previous, fingerprint):
                            record = load(previous / 'checkpoint.json')
                            if record['status'] == 'validated' or not args.execute:
                                records[page] = {**record, 'reused': True}
                                if record['status'] == 'validated':
                                    preflight_passed = True
                                pending.remove(page)
                                continue
                        prior = load(previous / 'checkpoint.json') if (previous/'checkpoint.json').is_file() else {}
                        if prior.get('fingerprint') != fingerprint:
                            raise ValueError(f'Page {page} source/draft/files changed; new run directory required')
                        if prior.get('status') != 'prepared' and page not in retries:
                            records[page] = {**prior, 'page': page, 'status': 'needs_review', 'reused': False}
                            pending.remove(page)
                            continue
                        if len(attempts) >= 2:
                            raise ValueError(f'Page {page} reached two attempts; inspect failure before a new run')
                    if args.execute and args.max_invocations is not None and historical_invocations + new_invocations >= args.max_invocations:
                        raise ValueError('CLI invocation budget exhausted; unfinished pages retained for explicit continuation')
                    new_invocations += int(args.execute)
                    pending.remove(page)
                    active[pool.submit(run_page, args, page, manifest_path, fingerprint, len(attempts)+1, draft)] = page
                signature = (tuple(sorted(pending)), final_data is not None)
                if active or signature != producer_signature:
                    producer_wait_started = None
                elif pending or final_data is None:
                    if producer_wait_started is None:
                        producer_wait_started = time.monotonic()
                    elif time.monotonic() - producer_wait_started > args.producer_timeout:
                        raise TimeoutError('Producer made no progress while workers were idle')
                producer_signature = signature
                publish('running' if active or pending else 'awaiting_full_manifest')
            except Exception as exc:
                fatal = f'{type(exc).__name__}: {exc}'
                break
            if pending or active or final_data is None:
                time.sleep(args.poll_seconds)
        for future, page in active.items():
            records[page] = future.result()
        active.clear()
    if source_digest(args.source_pdf) != source_hash:
        fatal = 'Source PDF changed while the run was executing'
    try:
        if args.format_profile and digest(args.format_profile) != contract['format_profile_sha256']:
            raise ValueError('Format profile changed while the run was executing')
        if args.execute and args.mode == 'typeset' and not args.preflight_mode:
            if check_template_ready(args.format_profile, args.template_ready, source_hash) != {k: v for k, v in format_metadata.items() if k != 'production'}:
                raise ValueError('Template admission changed while the run was executing')
    except (OSError, ValueError) as exc:
        fatal = str(exc)
    desired = 'validated' if args.execute or args.reuse_only else 'prepared'
    success = not fatal and not pending and final_data is not None and all(records[p]['status'] == desired for p in owned)
    status = ('complete' if owned == expected else 'assigned_complete') if success and (args.execute or args.reuse_only) else 'prepared' if success else 'incomplete'
    summary = publish(status)
    atomic_json(args.output / 'summary.json', summary)
    print(json.dumps({k: summary[k] for k in ('status', 'wall_seconds', 'model_invocations', 'validated_pages', 'failed_pages', 'error')}))
    raise SystemExit(0 if success else 1)


if __name__ == '__main__':
    main()
