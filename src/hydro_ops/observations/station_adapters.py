"""Read-only adapters for existing accepted CSV archives; no network acquisition."""
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

from .station_store import StationTable, write_station


def sync_archive(root, provider, station, resolution, *, target, lock_root, cdec_inventory=None):
    """Refresh a staged station from a complete processed-provider snapshot, not an API fetch."""
    root = Path(root).resolve()
    if not Path(target).resolve().is_relative_to(root/'observations/work'):
        raise ValueError('Pilot updates must stay under observations/work')
    table = archive_table(root, provider, station, resolution, cdec_inventory=cdec_inventory)
    result = write_station(target, table, lock_root=lock_root)
    return result, table


def archive_table(root, provider, station, resolution='daily', *, cdec_inventory=None):
    root = Path(root)
    sources = []
    def read(path):
        payload = path.read_bytes()
        sources.append({'path': str(path.relative_to(root)),
                        'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)})
        return payload
    def document(path):
        return json.loads(read(path))
    def rows(path):
        payload = read(path)
        if path.suffix == '.gz':
            payload = gzip.decompress(payload)
        reader = csv.DictReader(io.StringIO(payload.decode(), newline=''))
        return reader.fieldnames, list(reader)
    if provider == 'usgs':
        if resolution != 'daily':
            raise ValueError('USGS pilot supports daily discharge only')
        base = root/'observations/processed/usgs/discharge/daily'/station
        receipt = document(base/'acceptance.json')
        if receipt['status'] != 'passed' or receipt['monitoring_location_id'] != station:
            raise ValueError('USGS archive lacks matching acceptance')
        fields, records = rows(base/'history.csv.gz')
        auxiliary = {name: document(base/name) for name in ('station.json', 'diagnostics.json', 'plan.json')}
        auxiliary['annual_completeness.csv'] = rows(base/'annual_completeness.csv')[1]
        for source in sources:
            expected = receipt.get('outputs_sha256', {}).get(source['path'])
            if expected is not None and expected != source['sha256']:
                raise ValueError('USGS accepted source checksum changed')
        if any(r['monitoring_location_id'] != station for r in records):
            raise ValueError('Mixed USGS stations')
        metadata = {'provider': provider, 'station_id': station, 'product': 'discharge', 'sensor': '00060',
                    'resolution': resolution, 'units_si': 'm3 s-1',
                    'timezone': 'provider station timezone metadata; dates are not UTC instants',
                    'interval_semantics': 'Provider daily date labels; UTC interval bounds unverified',
                    'mapping': {'series': 'time_series_id', 'date': 'provider_date',
                                'value': 'discharge_m3_s', 'status': 'value_status'}}
    elif provider == 'cdec':
        if resolution not in ('daily', 'monthly') or cdec_inventory is None:
            raise ValueError('CDEC pilot requires daily/monthly FNF and explicit station inventory')
        base = root/'observations/processed/cdec/fnf'/resolution/station
        receipt = document(base/'manifest.json')
        if receipt['status'] != 'passed' or receipt['station_id'] != station or receipt['resolution'] != resolution:
            raise ValueError('CDEC archive lacks matching acceptance')
        inventory = document(root/cdec_inventory)
        matching = [r for r in inventory['stations'] if r['station_id'] == station]
        if inventory['status'] != 'passed' or len(matching) != 1:
            raise ValueError('CDEC station metadata missing or ambiguous')
        auxiliary = {'station.json': matching[0]}
        fields, records = None, []
        for path in sorted(base.glob('????.csv')):
            names, part = rows(path)
            if fields is not None and fields != names:
                raise ValueError('CDEC annual schema mismatch')
            fields = names
            records.extend(part)
        sensor, duration, units = ('8', 'D', 'm3 s-1') if resolution == 'daily' else ('65', 'M', 'm3')
        if any(r['station_id'] != station or r['sensor_number'] != sensor or
               r['duration_code'] != duration or r['units_si'] != units for r in records):
            raise ValueError('Mixed CDEC station/sensor/duration/units')
        metadata = {'provider': provider, 'station_id': station, 'product': 'full_natural_flow', 'sensor': sensor,
                    'resolution': resolution, 'units_si': units, 'timezone': receipt['timezone'],
                    'interval_semantics': receipt['interval_semantics'],
                    'mapping': {'series': 'sensor_number', 'date': 'period_label',
                                'value': 'value_si', 'status': 'value_status'}}
    else:
        raise ValueError('Unsupported pilot provider')
    if len(records) != receipt['rows']:
        raise ValueError('Accepted row count mismatch')
    # Detect source changes during assembly, including metadata and receipts.
    for source in sources:
        if hashlib.sha256((root/source['path']).read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('Source changed during conversion')
    metadata.update(archive_receipt=receipt, auxiliary=auxiliary, provenance=sources,
                    provenance_scope='Existing accepted processed archive; raw responses remain external')
    table = StationTable(metadata, fields, records)
    table.validate()
    return table
