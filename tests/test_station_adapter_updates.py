"""Synthetic provider-format revisions: exercise the real adapter, without API writes."""
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

import pytest

from hydro_ops.observations.station_adapters import sync_archive
from hydro_ops.observations.station_store import read_station


def test_cdec_adapter_extension_revision_missing_and_omission(tmp_path):
    base = tmp_path/'observations/processed/cdec/fnf/daily/ABC'
    base.mkdir(parents=True)
    inventory = tmp_path/'inventory.json'
    inventory.write_text(json.dumps({'status': 'passed', 'stations': [{'station_id': 'ABC', 'note': 'fixture'}]}))
    records = [{'station_id': 'ABC', 'sensor_number': '8', 'duration_code': 'D', 'units_si': 'm3 s-1',
                'period_label': f'2020-01-0{day}', 'value_si': str(day), 'value_status': 'numeric',
                'raw_value': str(day), 'data_flag': ' '} for day in (1, 2)]
    def provider_snapshot():
        with (base/'2020.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        (base/'manifest.json').write_text(json.dumps({
            'status': 'passed', 'station_id': 'ABC', 'resolution': 'daily', 'rows': len(records),
            'timezone': 'fixed PST', 'interval_semantics': 'unverified provider intervals'}))
    target = tmp_path/'observations/work/pilot/ABC.nc'
    kwargs = {'target': target, 'lock_root': tmp_path/'locks', 'cdec_inventory': Path('inventory.json')}
    provider_snapshot()
    assert sync_archive(tmp_path, 'cdec', 'ABC', 'daily', **kwargs)[0]['status'] == 'published'
    assert sync_archive(tmp_path, 'cdec', 'ABC', 'daily', **kwargs)[0]['status'] == 'unchanged'
    records[0].update(value_si='4', raw_value='4', data_flag='r')
    records[1].update(value_si='', raw_value='-9999', value_status='missing')
    records.append(dict(records[0], period_label='2020-01-03'))
    provider_snapshot()
    assert sync_archive(tmp_path, 'cdec', 'ABC', 'daily', **kwargs)[0]['status'] == 'published'
    assert read_station(target).rows == records
    before = target.read_bytes()
    records.pop(0)
    provider_snapshot()
    with pytest.raises(ValueError, match='History loss'):
        sync_archive(tmp_path, 'cdec', 'ABC', 'daily', **kwargs)
    assert target.read_bytes() == before
    with pytest.raises(ValueError, match='observations/work'):
        sync_archive(tmp_path, 'cdec', 'ABC', 'daily', **{**kwargs, 'target': base/'production.nc'})


def test_usgs_adapter_revision_and_acceptance_checksum(tmp_path):
    base = tmp_path/'observations/processed/usgs/discharge/daily/USGS-0012'
    base.mkdir(parents=True)
    for name in ('station.json', 'plan.json', 'diagnostics.json'):
        (base/name).write_text('{}')
    (base/'annual_completeness.csv').write_text('year,valid_days\n2020,1\n')
    records = [{'monitoring_location_id': 'USGS-0012', 'time_series_id': 'a',
                'provider_date': '2020-01-01', 'discharge_m3_s': '1', 'value_status': 'numeric',
                'raw_value': '35.3146667', 'qualifier': 'estimated'}]
    history = base/'history.csv.gz'
    def snapshot():
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
        history.write_bytes(gzip.compress(buffer.getvalue().encode(), mtime=0))
        (base/'acceptance.json').write_text(json.dumps({
            'status': 'passed', 'monitoring_location_id': 'USGS-0012', 'rows': len(records),
            'outputs_sha256': {str(history.relative_to(tmp_path)): hashlib.sha256(history.read_bytes()).hexdigest()}}))
    kwargs = {'target': tmp_path/'observations/work/pilot/USGS-0012.nc', 'lock_root': tmp_path/'locks'}
    snapshot()
    sync_archive(tmp_path, 'usgs', 'USGS-0012', 'daily', **kwargs)
    records[0].update(discharge_m3_s='2', qualifier='revised')
    records.append(dict(records[0], time_series_id='b', discharge_m3_s='3'))
    snapshot()
    sync_archive(tmp_path, 'usgs', 'USGS-0012', 'daily', **kwargs)
    assert read_station(kwargs['target']).rows == records
    before = kwargs['target'].read_bytes()
    receipt = json.loads((base/'acceptance.json').read_text())
    receipt['outputs_sha256'][str(history.relative_to(tmp_path))] = 'bad'
    (base/'acceptance.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='checksum changed'):
        sync_archive(tmp_path, 'usgs', 'USGS-0012', 'daily', **kwargs)
    assert kwargs['target'].read_bytes() == before
