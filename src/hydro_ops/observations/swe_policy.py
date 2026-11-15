"""CDEC-confirmed instrument classification requires station AND sensor/duration."""
import csv
import hashlib
import io
import re
from pathlib import Path

PAIRS = {'snow_course': ('3', 'monthly'), 'snow_pillow': ('82', 'daily')}
EXCLUSIONS = [{'station_id': 'BKL', 'sensor': '3', 'resolution': 'monthly',
               'reason': 'User verified on 2026-11-15 that CDEC has no sensor 3/monthly data'}]


def load_policy(root):
    allowed, sources = {}, []
    for method, (sensor, resolution) in PAIRS.items():
        path = Path(root)/'config/cdec_swe_stations'/f'{method}.csv'
        raw = path.read_bytes()
        ids = [r['STA'].strip() for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))]
        if not ids or len(ids) != len(set(ids)) or any(not re.fullmatch('[A-Z0-9]{3}', s) for s in ids):
            raise ValueError('Invalid or duplicate CDEC SWE station identifiers')
        allowed.update({(s, sensor, resolution): method for s in ids})
        sources.append({'path': str(path.relative_to(root)), 'sha256': hashlib.sha256(raw).hexdigest(),
                        'method': method, 'stations': len(ids)})
    for item in EXCLUSIONS:
        allowed.pop((item['station_id'], item['sensor'], item['resolution']), None)
    return allowed, {'version': 'cdec-confirmed-20261115-r2', 'sources': sources,
                     'exclusions': EXCLUSIONS,
                     'basis': 'User consultation with CDEC and supplied station lists; not inferred from sensor alone'}


def filter_swe(root, entries):
    allowed, report = load_policy(root)
    selected = [e for e in entries if (e['station_id'], str(e['sensor']), e['resolution']) in allowed]
    present = {(e['station_id'], str(e['sensor']), e['resolution']) for e in selected}
    report.update(selected=len(selected), excluded=len(entries)-len(selected),
                  missing_series=[{'station_id': s, 'sensor': n, 'resolution': r, 'method': allowed[s, n, r]}
                                  for s, n, r in sorted(set(allowed)-present)])
    return selected, report
