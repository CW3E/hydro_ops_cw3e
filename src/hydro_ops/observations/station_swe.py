"""Lossless, staging-only adapter for accepted CDEC SWE histories."""
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

from hydro_ops.download.cdec_swe import FIELDS, identity

from .station_store import StationTable, write_station


def swe_archive_table(root, station, sensor, resolution, *, inventory):
    root = Path(root).resolve()
    duration = {'daily': 'D', 'monthly': 'M', 'hourly': 'H', 'event': 'E'}[resolution]
    if sensor not in (3, 82):
        raise ValueError('Expected SWE sensor 3 or 82')
    base = root/'observations/processed/cdec/swe'/f'sensor_{sensor}'/resolution/station
    sources = []
    def read(path):
        data = path.read_bytes()
        sources.append({'path': str(path.relative_to(root)), 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest()})
        return data
    catalog = json.loads(read(root/inventory))
    matches = [s for s in catalog['stations'] if s['station_id'] == station]
    if catalog['status'] != 'passed' or len(matches) != 1 or not any(
            s['sensor_number'] == sensor and s['duration_code'] == duration and s['units'] == 'INCHES'
            for s in matches[0]['sensors']):
        raise ValueError('SWE station inventory mismatch')
    cursor = json.loads(read(base/'cursor.json'))
    receipt = json.loads(read(root/cursor['last_receipt']))
    if (receipt['status'] != 'passed' or receipt['station_id'] != station or
            receipt['sensor_number'] != sensor or receipt['duration_code'] != duration):
        raise ValueError('SWE acceptance identity mismatch')
    paths = [base/'history.csv.gz'] if resolution == 'monthly' else sorted(base.glob('????.csv.gz'))
    if not paths:
        raise ValueError('No SWE archive files')
    # Hourly/event acquisition is split by year. The cursor's last receipt can
    # belong to any completed chunk, not necessarily the latest observation.
    accepted = {}
    evidence = []
    receipt_dir = root/'observations/status/cdec/swe/chunks'/station/str(sensor)/duration
    candidates = sorted(receipt_dir.glob('*.json'))
    if not candidates:
        candidates = [root/cursor['last_receipt']]
    needed = {str(p.relative_to(root)): identity(p) for p in paths}
    for candidate in candidates:
        data = candidate.read_bytes()
        item = json.loads(data)
        if (item.get('status') != 'passed' or item.get('station_id') != station or
                item.get('sensor_number') != sensor or item.get('duration_code') != duration):
            continue
        matched_files = {name: ident for name, ident in item.get('output_identities', {}).items()
                   if name in needed and needed[name] == ident}
        if matched_files:
            accepted.update(matched_files)
            sources.append({'path': str(candidate.relative_to(root)), 'bytes': len(data),
                            'sha256': hashlib.sha256(data).hexdigest()})
            evidence.append(str(candidate.relative_to(root)))
    if accepted != needed:
        raise ValueError('SWE source lacks current accepted identity')
    records = []
    for path in paths:
        if accepted.get(str(path.relative_to(root))) != identity(path):
            raise ValueError('SWE source lacks current accepted identity')
        reader = csv.DictReader(io.StringIO(gzip.decompress(read(path)).decode(), newline=''))
        if reader.fieldnames != FIELDS:
            raise ValueError('SWE archive schema mismatch')
        records.extend(reader)
    if any(r['station_id'] != station or r['sensor_number'] != str(sensor) or
           r['duration_code'] != duration for r in records):
        raise ValueError('Mixed SWE station/sensor/duration')
    for source in sources:
        if hashlib.sha256((root/source['path']).read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('SWE source changed during conversion')
    table = StationTable({
        'provider': 'cdec', 'station_id': station, 'product': 'snow_water_equivalent',
        'sensor': str(sensor), 'resolution': resolution, 'units_si': 'mm',
        'timezone': 'fixed PST (UTC-08:00)', 'label_kind': 'fixed_pst_timestamp',
        'interval_semantics': 'Native SWE observations; sampling labels are not interval averages',
        'mapping': {'series': 'sensor_number', 'date': 'timestamp_pst',
                    'value': 'swe_mm', 'status': 'value_status'},
        'archive_receipt': receipt, 'accepted_chunk_receipts': evidence,
        'auxiliary': {'station.json': matches[0], 'cursor.json': cursor},
        'provenance': sources, 'provenance_scope': 'Accepted processed archive; raw responses remain external',
    }, FIELDS.copy(), records)
    table.validate()
    return table


def sync_swe_archive(root, station, sensor, resolution, *, inventory, target, lock_root):
    if not Path(target).resolve().is_relative_to(Path(root).resolve()/'observations/work'):
        raise ValueError('SWE pilot must remain under observations/work')
    table = swe_archive_table(root, station, sensor, resolution, inventory=inventory)
    return write_station(target, table, lock_root=lock_root), table
