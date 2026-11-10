"""Read-only discovery and metadata screening for a staged station migration.

Candidates are not validated conversions: adapters must recheck full payloads.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path


def fingerprint(path):
    stat = path.stat()
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns]


def discover(root):
    root = Path(root)
    patterns = [('usgs', 'discharge', '00060', 'observations/processed/usgs/discharge/daily/*'),
                ('cdec', 'full_natural_flow', None, 'observations/processed/cdec/fnf/*/*'),
                ('cdec', 'snow_water_equivalent', None, 'observations/processed/cdec/swe/*/*/*')]
    for provider, product, sensor, pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_dir():
                continue
            resolution = path.parent.name
            if resolution not in ('daily', 'monthly', 'hourly', 'event'):
                continue
            number = (path.parent.parent.name.removeprefix('sensor_') if product == 'snow_water_equivalent'
                      else ('8' if resolution == 'daily' else '65'))
            yield {'provider': provider, 'product': product, 'station_id': path.name,
                   'sensor': sensor or number, 'resolution': resolution,
                   'source': str(path.relative_to(root))}


def inspect(root, entry):
    root = Path(root).resolve()
    item = dict(entry)
    base = root/item['source']
    evidence = {}
    def document(path):
        if not path.resolve().is_relative_to(root):
            raise ValueError('Receipt path escapes project')
        before = fingerprint(path)
        data = path.read_bytes()
        if fingerprint(path) != before:
            raise ValueError('Receipt changed during scan')
        evidence[str(path.relative_to(root))] = {
            'identity': before, 'sha256': hashlib.sha256(data).hexdigest()}
        return json.loads(data)
    files = []
    try:
        product = item['product']
        if product == 'discharge':
            files = [base/'history.csv.gz']
            receipt = document(base/'acceptance.json')
            if receipt['status'] != 'passed' or receipt['monitoring_location_id'] != item['station_id']:
                raise ValueError('USGS acceptance mismatch')
            for name in ('station.json', 'diagnostics.json', 'plan.json', 'annual_completeness.csv'):
                p = base/name
                if not p.is_file():
                    raise ValueError(f'Missing {name}')
                evidence[str(p.relative_to(root))] = {'identity': fingerprint(p)}
            if str(files[0].relative_to(root)) not in receipt.get('outputs_sha256', {}):
                raise ValueError('USGS history lacks accepted checksum')
        elif product == 'full_natural_flow':
            files = sorted(base.glob('????.csv'))
            receipt = document(base/'manifest.json')
            if (receipt['status'] != 'passed' or receipt['station_id'] != item['station_id'] or
                    receipt['resolution'] != item['resolution']):
                raise ValueError('FNF acceptance mismatch')
        else:
            files = sorted(base.glob('*.csv.gz'))
            cursor = document(base/'cursor.json')
            receipt = document(root/cursor['last_receipt'])
            code = {'daily': 'D', 'monthly': 'M', 'hourly': 'H', 'event': 'E'}[item['resolution']]
            def matches(record):
                return (record.get('status') == 'passed' and record.get('station_id') == item['station_id']
                        and str(record.get('sensor_number')) == item['sensor'] and record.get('duration_code') == code)
            if not matches(receipt):
                raise ValueError('SWE acceptance mismatch')
            needed = {str(p.relative_to(root)): fingerprint(p) for p in files}
            accepted = {}
            receipts = root/'observations/status/cdec/swe/chunks'/item['station_id']/item['sensor']/code
            for path in sorted(receipts.glob('*.json')):
                candidate = document(path)
                if matches(candidate):
                    accepted.update({name: ident for name, ident in candidate.get('output_identities', {}).items()
                                     if name in needed and needed[name] == ident})
            if needed != accepted:
                raise ValueError('SWE files lack current accepted identities')
        if not files or not all(p.is_file() for p in files):
            item.update(status='empty_or_missing', reason='No complete set of source data files')
        else:
            item.update(status='candidate', reason='Metadata screening only; payload audit required')
    except (OSError, ValueError, KeyError, TypeError) as error:
        item.update(status='blocked', reason=f'{type(error).__name__}: {error}')
    item['data_files'] = {str(p.relative_to(root)): fingerprint(p) for p in files if p.is_file()}
    item['data_bytes'] = sum(v[1] for v in item['data_files'].values())
    item['evidence'] = evidence
    item['task_id'] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:24]
    return item


def inventory(root, *, pilot_limit=32):
    if not 1 <= pilot_limit <= 128:
        raise ValueError('Pilot limit must be 1–128')
    entries = [inspect(root, entry) for entry in discover(root)]
    # Round-robin provider/product/resolution groups; do not silently select only USGS.
    groups = {}
    for entry in entries:
        if entry['status'] == 'candidate':
            key = (entry['provider'], entry['product'], entry['sensor'], entry['resolution'])
            groups.setdefault(key, []).append(entry['task_id'])
    selected = []
    for index in range(pilot_limit):
        for group in sorted(groups):
            if index < len(groups[group]) and len(selected) < pilot_limit:
                selected.append(groups[group][index])
    return {'schema': 'hydro_ops_station_migration_inventory_v1',
            'scope': 'Discovered processed archives only; not all provider stations or network coverage',
            'source_modified': False, 'payload_checksums_verified': False,
            'counts': dict(Counter(e['status'] for e in entries)),
            'data_files': sum(len(e['data_files']) for e in entries),
            'data_bytes': sum(e['data_bytes'] for e in entries), 'entries': entries,
            'proposed_pilot': {'max_workers': 8, 'max_histories': pilot_limit,
                               'task_ids': selected, 'execute': False,
                               'destination': 'observations/work/station-migration/<campaign>/'}}
