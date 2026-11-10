"""Conservative incremental refresh of staged station histories.

Provider omissions are not deletions. Explicit missing records are revisions.
Acquisition receipts live separately so unchanged observations need no rewrite.
"""
from copy import deepcopy
from datetime import date
from pathlib import Path

from .station_store import StationTable, read_station, write_station


def acquire_window(root, raw, previous, *, start, end):
    """Fetch a short daily pilot window using existing provider normalization.

    Raw responses and receipt are staged before station publication. A fresh raw
    directory is mandatory; repeat-publication tests reuse the returned table.
    """
    import csv
    import gzip
    import hashlib
    import io
    from datetime import UTC, datetime, timedelta

    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    from hydro_ops.download import cdec_fnf, cdec_swe, usgs_discharge
    from hydro_ops.forcing.repair_transaction import atomic_json

    root, raw = Path(root).resolve(), Path(raw).resolve()
    if not raw.is_relative_to(root/'observations/work'):
        raise ValueError('Raw pilot must remain under observations/work')
    begin, stop = date.fromisoformat(start), date.fromisoformat(end)
    monthly = previous.metadata['resolution'] == 'monthly'
    if not 0 <= (stop-begin).days <= (366 if monthly else 31):
        raise ValueError('Pilot window exceeds resolution-specific limit')
    if monthly and (begin.day != 1 or (stop + timedelta(days=1)).day != 1):
        raise ValueError('Monthly acquisition requires complete calendar months')
    previous.validate()
    raw.mkdir(parents=True, exist_ok=False)
    provider = previous.metadata['provider']
    if provider == 'usgs':
        if previous.metadata['resolution'] != 'daily':
            raise ValueError('USGS daily only')
        spec = previous.metadata['auxiliary']['station.json']
        features, sources = usgs_discharge.daily_pages(
            root, raw, previous.metadata['station_id'], start, end)
        records = usgs_discharge.normalize(features, spec, start, end)
    elif provider == 'cdec':
        swe = previous.metadata['product'] == 'snow_water_equivalent'
        resolution = previous.metadata['resolution']
        duration = {'daily': 'D', 'monthly': 'M', 'hourly': 'H', 'event': 'E'}[resolution]
        spec = {'station_id': previous.metadata['station_id'], 'resolution': resolution,
                'start': start, 'request_start': start, 'request_end': end,
                'sensor_number': int(previous.metadata['sensor']), 'duration_code': duration}
        params = {'Stations': spec['station_id'], 'SensorNums': spec['sensor_number'],
                  'dur_code': duration, 'Start': start, 'End': end}
        if swe:
            params.update(Start=(begin-timedelta(days=1)).isoformat(),
                          End=(stop+timedelta(days=2)).isoformat())
        with requests.Session() as session:
            session.mount('https://', HTTPAdapter(max_retries=Retry(
                total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))))
            response = session.get(cdec_fnf.ENDPOINT, params=params, timeout=(20, 180))
            response.raise_for_status()
            payload = response.content
            compressed = gzip.compress(payload, compresslevel=4, mtime=0)
            cdec_fnf.atomic_bytes(raw/'response.json.gz', compressed)
            sources = [{'url': response.url, 'sha256': hashlib.sha256(payload).hexdigest(),
                        'compressed_sha256': hashlib.sha256(compressed).hexdigest(),
                        'raw_file': str((raw/'response.json.gz').relative_to(root))}]
            rows = response.json()
        if not isinstance(rows, list):
            raise ValueError('Malformed CDEC response')
        records = cdec_swe.normalize(rows, spec) if swe else cdec_fnf.normalize(rows, spec, stop)
    else:
        raise ValueError('Unsupported pilot provider')
    # Same serialization as existing CSV archives (None is an empty cell).
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=previous.fields)
    writer.writeheader()
    writer.writerows(records)
    buffer.seek(0)
    incoming = StationTable(deepcopy(previous.metadata), previous.fields.copy(),
                            list(csv.DictReader(buffer)))
    incoming.validate()
    receipt = {'status': 'acquired_not_published', 'provider': provider,
               'station_id': previous.metadata['station_id'], 'start': start, 'end': end,
               'retrieved_utc': datetime.now(UTC).isoformat(), 'sources': sources,
               'incoming_signature': incoming.signature(), 'rows': len(incoming.rows)}
    atomic_json(raw/'acquisition.json', receipt)
    return incoming, receipt


def acquire_daily_window(root, raw, previous, *, start, end):
    """Backward-compatible daily-only entry point."""
    if previous.metadata['resolution'] != 'daily':
        raise ValueError('Daily resolution required')
    return acquire_window(root, raw, previous, start=start, end=end)


def retain_omitted(previous, incoming, *, start, end):
    """Retain absent provider keys, with explicit evidence; never fill new gaps.

    The strict merger still validates identity/schema/series. Explicit provider
    missing values are present keys and therefore replace old numeric values.
    """
    mapping = previous.metadata['mapping']
    kind = previous.metadata.get('label_kind', 'period')
    def key(row):
        return row[mapping['series']], row[mapping['date']]
    present = {key(row) for row in incoming.rows}
    missing = [row for row in previous.rows
               if start <= (row[mapping['date']][:10] if kind == 'fixed_pst_timestamp'
                            else row[mapping['date']]) <= end and key(row) not in present]
    augmented = deepcopy(incoming)
    augmented.rows.extend(deepcopy(missing))
    merge_window(previous, augmented, start=start, end=end)
    return augmented, [{'series':key(row)[0], 'period':key(row)[1]} for row in missing]


def merge_window(previous, incoming, *, start, end):
    """Merge inclusive provider labels, requiring all known keys in the window."""
    previous.validate()
    incoming.validate()
    identity = ('provider', 'station_id', 'product', 'sensor', 'resolution', 'units_si',
                'timezone', 'interval_semantics', 'mapping')
    if any(previous.metadata[k] != incoming.metadata[k] for k in identity):
        raise ValueError('Station/series semantics changed')
    if previous.fields != incoming.fields:
        raise ValueError('Incremental source schema changed')
    kind = previous.metadata.get('label_kind', 'period')
    if incoming.metadata.get('label_kind', 'period') != kind:
        raise ValueError('Label semantics changed')
    monthly = previous.metadata['resolution'] == 'monthly' and kind == 'period'
    for label in (start, end):
        parsed = date.fromisoformat(label + '-01' if monthly else label)
        if label != (parsed.strftime('%Y-%m') if monthly else parsed.isoformat()):
            raise ValueError('Noncanonical refresh bounds')
    if start > end:
        raise ValueError('Reversed refresh window')
    mapping = previous.metadata['mapping']
    def key(row):
        return row[mapping['series']], row[mapping['date']]
    def inside(row):
        label = row[mapping['date']]
        return start <= (label[:10] if kind == 'fixed_pst_timestamp' else label) <= end
    if any(not inside(row) for row in incoming.rows):
        raise ValueError('Incoming observation outside refresh window')
    known = {key(row) for row in previous.rows if inside(row)}
    if not known <= {key(row) for row in incoming.rows}:
        raise ValueError('Incomplete refresh: known series/period keys omitted')
    allowed = {row[mapping['series']] for row in previous.rows}
    if any(row[mapping['series']] not in allowed for row in incoming.rows):
        raise ValueError('New series requires metadata review')
    updates = {key(row): row for row in incoming.rows}
    rows = [updates.pop(key(row), row) for row in previous.rows]
    rows.extend(updates.values())
    # Keep original ordering for a no-op, otherwise use deterministic date/series order.
    if rows == previous.rows:
        return deepcopy(previous)
    rows.sort(key=lambda row: (row[mapping['date']], row[mapping['series']]))
    metadata = deepcopy(previous.metadata)
    metadata['snapshot_scope'] = (
        'Incrementally refreshed observations; archive_receipt, auxiliary diagnostics and '
        'provenance describe the bootstrap snapshot only. Current counts are in the catalog; '
        'incremental acquisition receipts are external.')
    result = StationTable(metadata, previous.fields.copy(), deepcopy(rows))
    result.validate()
    return result


def refresh_station(root, target, incoming, *, start, end, lock_root):
    """Staging-only refresh with optimistic concurrency under the writer lock."""
    root, target = Path(root).resolve(), Path(target).resolve()
    if not target.is_relative_to(root/'observations/work'):
        raise ValueError('Incremental pilot must remain under observations/work')
    previous = read_station(target)
    merged = merge_window(previous, incoming, start=start, end=end)
    return write_station(target, merged, lock_root=lock_root,
                         expected_signature=previous.signature())
