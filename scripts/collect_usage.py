#!/usr/bin/env python3
"""Collect metadata from page/figure runs into a deduplicated, partial-aware ledger."""
import argparse
import hashlib
import json
from pathlib import Path


def normalize(tokens, semantics):
    tokens = tokens if isinstance(tokens, dict) else {}
    def integer(value):
        return value if type(value) is int and value >= 0 else None
    cache = tokens.get('cache', {})
    cache = cache if isinstance(cache, dict) else {}
    read, write = integer(cache.get('read')), integer(cache.get('write'))
    input_count = integer(tokens.get('input'))
    output, reasoning = integer(tokens.get('output')), integer(tokens.get('reasoning'))
    if semantics.get('input') == 'includes_cache':
        input_count = input_count-read-write if None not in (input_count, read, write) else None
        if input_count is not None and input_count < 0:
            raise ValueError('Returned input is less than declared cache counts')
    elif semantics.get('input') != 'uncached':
        input_count = None
    if semantics.get('output') == 'separate_reasoning':
        output = output+reasoning if None not in (output, reasoning) else None
    elif semantics.get('output') != 'includes_reasoning':
        output = None
    if output is not None and reasoning is not None and reasoning > output:
        raise ValueError('Reasoning exceeds normalized completion tokens')
    result = dict(input_uncached=input_count, cache_read=read, cache_write=write, output=output, reasoning=reasoning)
    result['partial'] = any(result[k] is None for k in ('input_uncached', 'cache_read', 'cache_write', 'output'))
    return result


def collect(paths, semantics_override=None):
    records, diagnostics, sources = {}, [], []
    for path in paths:
        path = Path(path).resolve(strict=True)
        data = json.loads(path.read_text())
        sources.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        if data.get('reused') or data.get('model_invocations') == 0 or data.get('paid_call_started') is False:
            continue
        semantics = (data.get('capabilities') or {}).get('declaration', {}).get('usage_semantics', {})
        preparation = path.parent/'run.json'
        if not semantics and path.name == 'checkpoint.json' and preparation.is_file():
            semantics = (json.loads(preparation.read_text()).get('capabilities') or {}).get('declaration', {}).get('usage_semantics', {})
        semantics = {**semantics, **(semantics_override or {})}
        items = data.get('session_usage') or data.get('returned_usage') or []
        if not items:
            diagnostics.append({'source': str(path), 'reason': 'no_request_usage_available; failed/incomplete call may still be billed'})
        if data.get('session_usage_partial') or data.get('usage_partial') or data.get('timed_out'):
            diagnostics.append({'source': str(path), 'reason': 'partial_or_failed_call; known usage retained'})
        for index, item in enumerate(items):
            tokens = item.get('tokens_as_returned')
            raw_usage = None
            if tokens is None and any(key in item for key in ('input_tokens', 'output_tokens', 'cached_input_tokens')):
                # Codex emits a flat turn usage object, without a stable
                # provider request ID. Retain its known counts; in particular
                # do not invent a zero cache-write count to make it complete.
                raw_usage = {key: item[key] for key in ('input_tokens', 'output_tokens',
                             'cached_input_tokens', 'reasoning_output_tokens')
                             if type(item.get(key)) is int and item[key] >= 0}
                tokens = {'input': raw_usage.get('input_tokens'), 'output': raw_usage.get('output_tokens'),
                          'reasoning': raw_usage.get('reasoning_output_tokens'),
                          'cache': {'read': raw_usage.get('cached_input_tokens')}}
            message, session, part = item.get('message_id'), item.get('session_id'), item.get('part_id')
            if message and session:
                ident, unit = f'opencode:{session}:{message}', 'assistant_message'
            elif part:
                ident, unit = f'opencode-step:{part}', 'cli_step_not_verified_request'
            else:
                ident, unit = f'unidentified:{sources[-1]["sha256"]}:{index}', 'unidentified_usage_record'
            record = {'id': ident, 'record_unit': unit,
                      **normalize(tokens, semantics),
                      'semantics': semantics, 'actual_cost': None, 'currency': None,
                      'tokens_as_returned': tokens, 'raw_flat_usage': raw_usage,
                      'opencode_reported_cost': item.get('opencode_reported_cost', item.get('opencode_catalogue_cost')),
                      'cost_status': 'CLI metadata; not verified provider billing',
                      'actual_models': data.get('actual_models', []), 'sources': [str(path)]}
            if unit != 'assistant_message':
                record['partial'] = True
            previous = records.get(ident)
            if previous:
                if any(previous[k] != record[k] for k in ('tokens_as_returned', 'semantics', 'opencode_reported_cost')):
                    raise ValueError('Conflicting usage for the same message/step identity')
                previous['sources'].append(str(path))
            else:
                records[ident] = record
    result = list(records.values())
    return {'schema_version': 1, 'records': result, 'source_files': sources, 'diagnostics': diagnostics,
            'usage_partial': bool(diagnostics) or not result or any(x['partial'] for x in result),
            'record_count': len(result), 'provider_request_count': None,
            'note': 'Deduplicated local message/step metadata; provider request IDs, hidden retries and billed totals are not reconciled'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, action='append', required=True, help='Coordinator run directory, or standalone run.json/checkpoint.json')
    parser.add_argument('--input-semantics', choices=['uncached', 'includes_cache', 'unknown'])
    parser.add_argument('--output-semantics', choices=['includes_reasoning', 'separate_reasoning', 'unknown'])
    parser.add_argument('--output', type=Path, required=True, help='New directory with ledger.json and normalized-records.json')
    args = parser.parse_args()
    paths = []
    for run in args.run:
        if run.is_dir():
            paths.extend(sorted((run/'pages').glob('*/attempt-*/checkpoint.json')))
        else:
            # Older coordinator versions left preparation-only run.json beside
            # the authoritative execution checkpoint. Prefer that checkpoint.
            paths.append(run.parent/'checkpoint.json' if run.name == 'run.json' and (run.parent/'checkpoint.json').is_file() else run)
    semantics = {k: v for k, v in [('input', args.input_semantics), ('output', args.output_semantics)] if v}
    result = collect(sorted(set(paths)), semantics)
    if args.output.exists():
        parser.error('Ledger output exists; use a new revision')
    args.output.mkdir(parents=True)
    (args.output/'ledger.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (args.output/'normalized-records.json').write_text(json.dumps(result['records'], ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ('record_count', 'provider_request_count', 'usage_partial')}))


if __name__ == '__main__':
    main()
