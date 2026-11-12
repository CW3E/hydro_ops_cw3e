"""Conservative, read-only legacy cleanup proofs. No deletion functions."""
import re
from pathlib import Path


def legacy_payload(path):
    p = Path(path)
    parts = p.parts
    if parts[:4] == ('observations', 'processed', 'usgs', 'discharge'):
        return p.name == 'history.csv.gz'
    if parts[:4] in (('observations', 'processed', 'cdec', 'fnf'),
                     ('observations', 'processed', 'cdec', 'swe')):
        return bool(re.fullmatch(r'\d[^/]*\.csv(?:\.gz)?', p.name))
    return False


def compare_history(old, current):
    """Require every old field/flag/value; legitimate revisions still need review."""
    keys = ('provider', 'station_id', 'product', 'sensor', 'resolution', 'units_si',
            'timezone', 'interval_semantics', 'mapping')
    incompatible = [k for k in keys if old.metadata.get(k) != current.metadata.get(k)]
    if incompatible or not set(old.fields) <= set(current.fields):
        return {'status': 'retain_review', 'reason': 'schema_or_metadata_difference', 'fields': incompatible}
    mapping = old.metadata['mapping']
    def key(row):
        return row[mapping['series']], row[mapping['date']]
    lookup = {key(row): row for row in current.rows}
    missing, changed = [], []
    for row in old.rows:
        other = lookup.get(key(row))
        if other is None:
            missing.append(key(row))
        elif any(row[f] != other[f] for f in old.fields):
            changed.append(key(row))
    return {'status': 'eligible' if not missing and not changed else 'retain_review',
            'legacy_rows': len(old.rows), 'current_rows': len(current.rows),
            'missing_count': len(missing), 'changed_count': len(changed),
            'missing_examples': missing[:20], 'changed_examples': changed[:20],
            'reason': 'Exact historical fields and flags retained' if not missing and not changed
                else 'Differences may be legitimate provider revisions; retain until reviewed'}
