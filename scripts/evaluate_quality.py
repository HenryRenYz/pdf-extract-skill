#!/usr/bin/env python3
"""Evaluate independently aligned source checkpoints; never claim full-page accuracy."""
import argparse
import itertools
import json
from pathlib import Path
from source_identity import file_hash, manifest_source_hash
from table_data import table_rows


def distance(a, b):
    previous = list(range(len(b)+1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(left != right)))
        previous = current
    return previous[-1]


def table_grid(block):
    occupied, anchors = set(), {}
    rows = table_rows(block)
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in occupied:
                c += 1
            if r+cell.rowspan > len(rows):
                raise ValueError('Table rowspan extends beyond the last row')
            area = {(y, x) for y in range(r, r+cell.rowspan) for x in range(c, c+cell.colspan)}
            if area & occupied:
                raise ValueError('Overlapping table spans')
            occupied |= area
            anchors[(r, c)] = (cell.text, cell.rowspan, cell.colspan)
            c += cell.colspan
    height = max((r for r, c in occupied), default=-1)+1
    width = max((c for r, c in occupied), default=-1)+1
    if len(occupied) != height*width:
        raise ValueError('Table has uncovered grid positions')
    return anchors, [height, width]


def style_sets(text, spans):
    result = {}
    for span in spans:
        start, end, kind = span.get('start'), span.get('end'), span.get('kind')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or not isinstance(kind, str):
            raise ValueError('Invalid Unicode style span')
        if kind != 'normal':
            result.setdefault(kind, set()).update(range(start, end))
    return result


def evaluate(reference, candidate, alignment):
    """Pure scoring. File/source/reviewer identities are checked by the CLI."""
    blocks = candidate.get('blocks')
    if not isinstance(blocks, list) or len({b['id'] for b in blocks}) != len(blocks):
        raise ValueError('Candidate needs unique block ids')
    by_id = {b['id']: (index, b) for index, b in enumerate(blocks)}
    mapping = alignment.get('units', {})
    checkpoints = reference['checkpoints']
    if len({c['id'] for c in checkpoints}) != len(checkpoints):
        raise ValueError('Duplicate checkpoint IDs')
    known = {c['id'] for c in checkpoints if c['dimension'] != 'reading_order'}
    if set(mapping)-known:
        raise ValueError('Alignment contains unknown checkpoint ids')
    def locate(ident):
        row = mapping.get(ident)
        if row is None:
            return None
        if not isinstance(row, dict) or row.get('block_id') not in by_id:
            raise ValueError('Alignment references an absent candidate block')
        index, block = by_id[row['block_id']]
        text = block['text']
        start, end = row.get('start', 0), row.get('end', len(text))
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
            raise ValueError('Alignment has invalid Unicode text slice')
        return index, block, start, end, text[start:end]
    result = {'text': {'checkpoints': 0, 'exact': 0, 'edit_distance': 0, 'reference_characters': 0},
              'formula': {'checkpoints': 0, 'strict_exact': 0, 'accepted_exact': 0},
              'table': {'checkpoints': 0, 'shape_exact': 0, 'reference_cells': 0, 'candidate_cells': 0,
                        'cell_text_exact': 0, 'cell_text_and_span_exact': 0, 'parse_failures': 0},
              'styles': {'checkpoints': 0, 'text_aligned_checkpoints': 0, 'exact_checkpoints': 0,
                         'reference_characters': 0, 'scored_characters': 0, 'by_kind': {}, 'by_attribute': {}},
              'reading_order': {'checkpoints': 0, 'reference_units': 0, 'aligned_units': 0,
                                'reference_pairs': 0, 'comparable_pairs': 0, 'concordant_pairs': 0}}
    details = []
    for checkpoint in checkpoints:
        dimension = checkpoint['dimension']
        if dimension not in result:
            raise ValueError('Unknown quality dimension')
        score = result[dimension]
        score['checkpoints'] += 1
        ident = checkpoint['id']
        if dimension == 'reading_order':
            units = checkpoint['units']
            if len(units) < 2 or len(set(units)) != len(units) or not set(units) <= known:
                raise ValueError('Order checkpoint requires distinct known units')
            locations = [locate(u) for u in units]
            score['reference_units'] += len(units)
            score['aligned_units'] += sum(x is not None for x in locations)
            for left, right in itertools.combinations(locations, 2):
                score['reference_pairs'] += 1
                if left is not None and right is not None:
                    # Two regions of one block are comparable only if their
                    # character ranges do not overlap. Identical mappings do
                    # not manufacture a correct reading-order pair.
                    if left[0] != right[0] or left[3] <= right[2] or right[3] <= left[2]:
                        score['comparable_pairs'] += 1
                        score['concordant_pairs'] += int(left[0] < right[0] or left[0] == right[0] and left[3] <= right[2])
            continue
        found = locate(ident)
        actual = found[4] if found else ''
        exact = False
        if dimension == 'text':
            expected = checkpoint['text']
            if not isinstance(expected, str) or not expected:
                raise ValueError('Text checkpoints need nonempty literal references')
            score['reference_characters'] += len(expected)
            score['edit_distance'] += distance(expected, actual)
            exact = found is not None and actual == expected
            score['exact'] += int(exact)
        elif dimension == 'formula':
            expected = checkpoint['text']
            if not isinstance(expected, str) or not expected:
                raise ValueError('Formula references must be nonempty literal strings')
            exact = found is not None and actual == expected
            score['strict_exact'] += int(exact)
            score['accepted_exact'] += int(found is not None and actual in [expected, *checkpoint.get('accepted_variants', [])])
        elif dimension == 'table':
            expected, shape = table_grid({'cells': checkpoint['cells'], 'text': ''})
            if not expected:
                raise ValueError('Table references must contain cells')
            score['reference_cells'] += len(expected)
            observed, observed_shape = {}, None
            if found:
                try:
                    observed, observed_shape = table_grid(found[1])
                except ValueError:
                    score['parse_failures'] += 1
            score['candidate_cells'] += len(observed)
            score['shape_exact'] += int(shape == observed_shape)
            for position, cell in expected.items():
                other = observed.get(position)
                score['cell_text_exact'] += int(other is not None and cell[0] == other[0])
                score['cell_text_and_span_exact'] += int(cell == other)
            exact = shape == observed_shape and expected == observed
        elif dimension == 'styles':
            text = checkpoint['text']
            expected = style_sets(text, checkpoint['styles'])
            score['reference_characters'] += len(text)
            if found and actual == text:
                spans = []
                for span in found[1].get('styles', []):
                    # Validate full-block spans before clipping to the aligned region.
                    style_sets(found[1]['text'], [span])
                    start, end = max(span['start'], found[2]), min(span['end'], found[3])
                    if start < end:
                        spans.append({**span, 'start': start-found[2], 'end': end-found[2]})
                observed = style_sets(text, spans)
                score['text_aligned_checkpoints'] += 1
                score['scored_characters'] += len(text)
                exact = expected == observed
                score['exact_checkpoints'] += int(exact)
                for kind in set(expected)|set(observed):
                    gold, pred = expected.get(kind, set()), observed.get(kind, set())
                    counts = score['by_kind'].setdefault(kind, dict(tp=0, fp=0, fn=0))
                    counts['tp'] += len(gold & pred)
                    counts['fp'] += len(pred-gold)
                    counts['fn'] += len(gold-pred)
                for attribute, kinds in {'bold': {'bold', 'bold_italic', 'bold_math'},
                                         'italic': {'italic', 'bold_italic'}}.items():
                    gold = set().union(*(expected.get(k, set()) for k in kinds))
                    pred = set().union(*(observed.get(k, set()) for k in kinds))
                    counts = score['by_attribute'].setdefault(attribute, dict(tp=0, fp=0, fn=0))
                    counts['tp'] += len(gold & pred)
                    counts['fp'] += len(pred-gold)
                    counts['fn'] += len(gold-pred)
        details.append({'id': ident, 'dimension': dimension, 'aligned': found is not None, 'exact': exact})
    text = result['text']
    text['cer'] = text['edit_distance']/text['reference_characters'] if text['reference_characters'] else None
    for counts in [*result['styles']['by_kind'].values(), *result['styles']['by_attribute'].values()]:
        counts['precision'] = counts['tp']/(counts['tp']+counts['fp']) if counts['tp']+counts['fp'] else None
        counts['recall'] = counts['tp']/(counts['tp']+counts['fn']) if counts['tp']+counts['fn'] else None
    return {'schema_version': 1, 'dimensions': result, 'checkpoints': details,
            'scope': 'independently aligned checkpoints only; literal Unicode with no whitespace/LaTeX normalization; no full-page accuracy claim',
            'style_scope': 'precision/recall conditioned on exact reference text; unaligned text excluded and coverage reported',
            'order_scope': 'reference/comparable pair denominators both reported; missing units never counted correct'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('reference', 'candidate', 'alignment', 'manifest', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    reference, candidate, alignment, manifest = [json.loads(p.read_text()) for p in (args.reference, args.candidate, args.alignment, args.manifest)]
    if reference.get('schema_version') != 1 or alignment.get('schema_version') != 1:
        parser.error('Unsupported benchmark schema')
    if alignment.get('reference_sha256') != file_hash(args.reference) or alignment.get('candidate_sha256') != file_hash(args.candidate):
        parser.error('Alignment must bind the exact reference and candidate bytes')
    if not alignment.get('reviewer') or not reference.get('reviewer'):
        parser.error('Independent source reference and candidate alignment need reviewer identities')
    if candidate.get('source_sha256', reference.get('source_sha256')) != reference.get('source_sha256') or candidate.get('source_image_sha256', reference.get('source_image_sha256')) != reference.get('source_image_sha256'):
        parser.error('Candidate embedded source identity differs')
    if manifest_source_hash(manifest) != reference.get('source_sha256'):
        parser.error('Benchmark source differs')
    page = next((p for p in manifest['pages'] if p['page'] == candidate.get('page')), None)
    if page is None or page['page'] != reference.get('page') or page['original']['sha256'] != reference.get('source_image_sha256'):
        parser.error('Benchmark page/image differs')
    evidence = (args.reference.parent/reference['evidence']['path']).resolve(strict=True)
    source_image = (args.manifest.parent/page['original']['path']).resolve(strict=True)
    if file_hash(evidence) != reference['evidence']['sha256'] or file_hash(source_image) != reference['source_image_sha256']:
        parser.error('Benchmark source evidence changed')
    if reference['evidence']['sha256'] != reference['source_image_sha256']:
        parser.error('Benchmark evidence must be the exact prepared source page image')
    result = evaluate(reference, candidate, alignment)
    result.update(reference_sha256=file_hash(args.reference), candidate_sha256=file_hash(args.candidate),
                  alignment_sha256=file_hash(args.alignment), source_sha256=reference['source_sha256'],
                  source_image_sha256=reference['source_image_sha256'], page=reference['page'])
    if args.output.exists():
        parser.error('Evaluation output exists; choose a new revision')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result['dimensions'], ensure_ascii=False))


if __name__ == '__main__':
    main()
