#!/usr/bin/env python3
"""Sum normalized request usage; never infer provider token semantics or prices."""
import argparse
from collections import Counter
import json
from decimal import Decimal
from pathlib import Path

FIELDS = ('input_uncached', 'cache_read', 'cache_write', 'output', 'reasoning')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', required=True, type=Path, help='JSON array of normalized requests')
    parser.add_argument('--prices', type=Path, help='currency/as_of plus per_million field prices')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    records = json.loads(args.records.read_text())
    if not isinstance(records, list):
        parser.error('Records must be a JSON list')
    ids = [record['id'] for record in records]
    if len(set(ids)) != len(ids):
        parser.error('Duplicate request IDs; remove overlapping exports first')
    totals = dict.fromkeys(FIELDS, 0)
    known = dict.fromkeys(FIELDS, 0)
    partial = not records
    actual = Decimal(0)
    currencies = set()
    known_actual = 0
    for record in records:
        partial |= record.get('partial', False)
        for field in FIELDS:
            value = record.get(field)
            if value is None:
                if field != 'reasoning':
                    partial = True
                continue
            if type(value) is not int or value < 0:
                parser.error(f'{record["id"]}: {field} must be a nonnegative integer or null')
            totals[field] += value
            known[field] += 1
        if record.get('reasoning') is not None and record.get('output') is not None:
            if record['reasoning'] > record['output']:
                parser.error('Normalized output must include reasoning; reasoning cannot exceed output')
        if record.get('actual_cost') is not None:
            if not record.get('currency'):
                parser.error('actual_cost requires currency')
            value = Decimal(str(record['actual_cost']))
            if not value.is_finite() or value < 0:
                parser.error('actual_cost must be finite and nonnegative')
            currencies.add(record['currency'])
            actual += value
            known_actual += 1
    if len(currencies) > 1:
        parser.error('Do not mix currencies in a single accounting run')
    units = Counter(r.get('record_unit', 'provider_request') for r in records)
    result = {'requests': len(records) if set(units) <= {'provider_request'} else None,
              'usage_records': len(records), 'counting_units': dict(units),
              'tokens': {k: totals[k] if records and known[k] == len(records) else None for k in FIELDS},
              'tokens_known_subtotals': {k: totals[k] if known[k] else None for k in FIELDS},
              'token_field_coverage': {k: {'known_records': known[k], 'records': len(records)} for k in FIELDS},
              'total_tokens_known': sum(totals[k] for k in FIELDS if k != 'reasoning'),
              'usage_partial': partial,
              'actual_cost_known': str(actual) if known_actual else None,
              'actual_cost_currency': next(iter(currencies), None),
              'actual_cost_records': known_actual,
              'actual_cost_complete': bool(records) and known_actual == len(records)}
    if args.prices:
        prices = json.loads(args.prices.read_text())
        if not prices.get('currency') or not prices.get('as_of'):
            parser.error('Prices require currency and as_of; do not use stale implicit prices')
        rates = prices['per_million']
        estimated = Decimal(0)
        for field in FIELDS[:-1]:
            if field not in rates:
                parser.error(f'Missing explicit price for {field}; use 0 only when actually free')
            rate = Decimal(str(rates[field]))
            if not rate.is_finite() or rate < 0:
                parser.error('Prices must be finite and nonnegative')
            estimated += Decimal(totals[field]) * rate / Decimal(1_000_000)
        result['estimate'] = {'cost_known': str(estimated), 'currency': prices['currency'],
                              'price_as_of': prices['as_of'], 'partial': partial}
    if args.output.exists():
        parser.error('Output already exists; choose a new path')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
