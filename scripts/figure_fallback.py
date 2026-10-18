"""Validate why a selected illustration must keep source pixels.

Technical checks cannot prove visual fidelity. They can prevent an unexplained
legacy fallback or a time/budget decision from being published as final QA.
"""
import hashlib
from pathlib import Path


def validate_fallback(figure, asset_path=None):
    assessment = figure.get('fallback_assessment')
    if not isinstance(assessment, dict):
        raise ValueError('Retained illustration needs fallback_assessment')
    if assessment.get('status') != 'source_checked':
        raise ValueError('Retained illustration feasibility must be source_checked')
    if assessment.get('category') not in {'photograph', 'complex_rendering', 'unrecoverable_source', 'reconstruction_failed',
                                         'user_selected_original', 'extraction_preserve'}:
        raise ValueError('Density, time, cost or an untried backend do not justify source pixels')
    if assessment['category'] == 'user_selected_original' and assessment.get('user_requested') is not True:
        raise ValueError('User-selected original requires an explicit recorded user choice')
    if assessment['category'] == 'extraction_preserve' and assessment.get('workflow_mode') not in {'extract', 'proofread'}:
        raise ValueError('Extraction preservation requires extract/proofread mode')
    for key in ('reviewer', 'reason'):
        if not isinstance(assessment.get(key), str) or not assessment[key].strip():
            raise ValueError('Retained illustration requires ' + key)
    evidence = assessment.get('evidence')
    if not isinstance(evidence, dict) or not isinstance(evidence.get('path'), str):
        raise ValueError('Retained illustration requires source evidence path/hash')
    path = Path(evidence['path']).expanduser()
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != evidence.get('sha256'):
        raise ValueError('Retained illustration source evidence missing/changed')
    if asset_path is not None:
        if hashlib.sha256(Path(asset_path).read_bytes()).hexdigest() != assessment.get('asset_sha256'):
            raise ValueError('Retained illustration assessment does not bind selected pixels')
    if assessment['category'] in {'unrecoverable_source', 'reconstruction_failed'}:
        if not isinstance(assessment.get('missing_details'), list) or not assessment['missing_details']:
            raise ValueError('Fallback requires specific unrecoverable details')
        alternatives = assessment.get('alternatives')
        if not isinstance(alternatives, list) or not alternatives or any(
            not isinstance(item, dict) or not item.get('method') or not item.get('limitation') for item in alternatives
        ):
            raise ValueError('Fallback requires evaluated native/code/vector alternatives')
    if assessment['category'] == 'reconstruction_failed':
        attempts = assessment.get('attempts')
        if not isinstance(attempts, list) or not attempts:
            raise ValueError('Failed reconstruction needs actual attempt evidence')
        for attempt in attempts:
            if not isinstance(attempt, dict) or not attempt.get('defect'):
                raise ValueError('Failed reconstruction needs a concrete fidelity defect')
            artifact = Path(attempt.get('path', ''))
            if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != attempt.get('sha256'):
                raise ValueError('Failed reconstruction attempt missing/changed')
    return assessment
