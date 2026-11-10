"""Partition an accepted inventory into disjoint, bounded conversion batches."""
from .station_conversion import select_batch


def batches(report, excluded=(), size=128):
    candidates = {e['task_id'] for e in report['entries'] if e['status'] == 'candidate'}
    seen = set(excluded)
    if not seen <= candidates:
        raise ValueError('Excluded tasks are not inventory candidates')
    result = []
    while candidates-seen:
        selected = select_batch(report, size=size, excluded=seen)
        result.append(selected)
        seen.update(selected)
    return result
