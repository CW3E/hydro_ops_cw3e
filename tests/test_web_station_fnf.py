import csv
import gzip
import io
import json
from types import SimpleNamespace

import pytest

from hydro_ops.download.cdec_fnf import PRODUCTS
from hydro_ops.web import station_fnf as mod
from hydro_ops.web.globus_transfer import digest


def row(resolution='daily', value='-2'):
    sensor, duration, native, factor, unit = PRODUCTS[resolution]
    return {'station_id': 'ABC', 'sensor_number': str(sensor), 'duration_code': duration,
            'units': native, 'units_si': unit, 'value': value, 'raw_value': value or '-9999',
            'value_si': str(float(value)*factor) if value else '',
            'value_status': ('negative' if float(value) < 0 else 'numeric') if value else 'missing_sentinel_-9999',
            'period_label': '2024-02-29' if resolution == 'daily' else '2024-02',
            'timestamp_pst': '2024-02-29T00:00:00-08:00', 'data_flag': 'r'}


@pytest.mark.parametrize('resolution', ['daily', 'monthly'])
@pytest.mark.parametrize('value,status', [('-2', 'negative_retained'), ('0', 'retained'), ('', 'missing')])
def test_native_semantics(resolution, value, status):
    original = row(resolution, value)
    result = mod.export_rows(SimpleNamespace(rows=[original]), 'ABC', resolution)[0]
    assert result['display_status'] == status
    assert result['fnf_display_si'] == original['value_si']
    assert all(result[k] == v for k, v in original.items())


def test_reject_invalid():
    original = row()
    with pytest.raises(ValueError, match='Duplicate'):
        mod.export_rows(SimpleNamespace(rows=[original, original]), 'ABC', 'daily')
    for changes in ({'value_si': 'nan'}, {'value_si': '10'}, {'raw_value': '-9999'},
                    {'timestamp_pst': '2024-02-29T00:00:00-07:00'}, {'units': 'AF'}):
        with pytest.raises(ValueError):
            mod.export_rows(SimpleNamespace(rows=[dict(original, **changes)]), 'ABC', 'daily')


def test_manifest_no_swe_or_nonnegative_assumptions():
    for resolution, variable, unit in [('daily', 'fnf_flow', 'm3 s-1'), ('monthly', 'fnf_volume', 'm3')]:
        doc = mod.manifest(resolution, 'test', [], [])
        assert doc['variables'][variable]['unit'] == unit
        assert doc['variables'][variable]['validRange']['minimum'] is None
        assert not doc['quality']['displayStatusDictionary']['negative_retained']['exclude']
        assert 'swe' not in doc['variables']
        assert doc['source']['kind'] == 'derived_estimate'
        assert doc['time']['observationTimeLabel'] == 'Provider observation timestamp'
        assert doc['csv']['absentPeriodPolicy']['connectgapsFalseAloneSufficient'] is False


def test_flags_and_absent_period():
    flags = mod.flag_dictionary([{'coverage': {'providerFlagCounts': {'N': 1, 'X': 2, 'r': 3, 'R': 1}}}])
    assert flags['N']['meaning'] == 'Error in data'
    assert flags['X']['definitionStatus'] == 'unresolved'
    assert flags['R']['definitionStatus'] == 'unresolved'
    assert all(not flag['exclude'] for flag in flags.values())
    rows = [dict(row(), period_label='1996-06-12'), dict(row(), period_label='1996-06-14')]
    gaps = mod.gap_references(rows, 'daily', 'test.csv.gz')
    assert gaps[0]['firstAbsentPeriod'] == '1996-06-13'
    assert gaps[0]['expectedPlot']['breakLine']
    assert not mod.gap_references([dict(row(), period_label='2024-02-28'), row()], 'daily', 'test')
    monthly = [dict(row('monthly'), period_label='2023-12'), dict(row('monthly'), period_label='2024-02')]
    assert mod.gap_references(monthly, 'monthly', 'test')[0]['firstAbsentPeriod'] == '2024-01'


@pytest.mark.parametrize('change', ['none', 'unrelated', 'fnf', 'source'])
def test_full_build(tmp_path, monkeypatch, change):
    base = tmp_path/'observations/processed/stations/v1'
    base.mkdir(parents=True)
    (tmp_path/'config').mkdir()
    (tmp_path/'config/cdec_fnf_stations.json').write_text(json.dumps({'station_ids': ['ABC', 'TNL']}))
    entries, tables = [], {}
    for resolution in PRODUCTS:
        for station in (['ABC'] if resolution == 'daily' else ['ABC', 'TNL']):
            source = base/f'{station}-{resolution}.nc'
            source.write_text(station+resolution)
            original = dict(row(resolution), station_id=station)
            sensor, _, _, _, unit = PRODUCTS[resolution]
            tables[source] = SimpleNamespace(rows=[original], fields=list(original), metadata={
                'provider': 'cdec', 'product': 'full_natural_flow', 'sensor': str(sensor),
                'resolution': resolution, 'units_si': unit,
                'auxiliary': {'station.json': {'longitude': -120, 'latitude': 38,
                    'station_name': station, 'river_basin': 'test', 'elevation_raw': '1 ft',
                    'source_url': 'https://example.org', 'historical_notes': []}}})
            entries.append({'provider': 'cdec', 'product': 'full_natural_flow', 'resolution': resolution,
                            'station_id': station, 'path': source.name, 'sha256': digest(source), 'rows': 1})
    (base/'catalog.json').write_text(json.dumps({'entries': entries}))
    def read(path):
        if change == 'source':
            path.write_text('changed')
        elif change in ('unrelated', 'fnf'):
            current = json.loads((base/'catalog.json').read_text())
            if change == 'unrelated':
                current['entries'].append({'provider': 'usgs', 'product': 'streamflow'})
            else:
                current['entries'][0]['rows'] += 1
            (base/'catalog.json').write_text(json.dumps(current))
        return tables[path]
    monkeypatch.setattr(mod, 'read_station', read)
    output = tmp_path/'web/publications/observations/test'
    if change in ('fnf', 'source'):
        with pytest.raises(ValueError, match='changed'):
            mod.build(tmp_path, output)
        assert not (output/'acceptance.json').exists()
        return
    report = mod.build(tmp_path, output)
    assert report['status'] == 'passed'
    assert [layer['stations'] for layer in report['layers']] == [1, 2]
    assert 'catalog_sha256' not in report
    mod.verify_catalog_hash(output, report)
    with pytest.raises(ValueError, match='Contradictory'):
        mod.verify_catalog_hash(output, dict(report, publication_catalog_sha256='bad'))
    for item in report['files']:
        assert digest(output/item['path']) == item['sha256']
    data = output/'daily/data/ABC/sensor-8/history.csv.gz'
    rows = list(csv.DictReader(io.StringIO(gzip.decompress(data.read_bytes()).decode(), newline='')))
    assert rows[0]['display_status'] == 'negative_retained'
    with pytest.raises(FileExistsError):
        mod.build(tmp_path, output)


def test_update_receipt_identity(tmp_path):
    work = tmp_path/'observations/work/station-cycles/test'
    (work/'0').mkdir(parents=True)
    entry = {'path': 'cdec/ABC/fnf/8/daily.nc', 'sha256': 'abc'}
    receipt = {'status': 'passed', 'result': {'entry': entry}, 'retrieved_utc': '2026-11-15T00:00:00Z'}
    (work/'0/publication.json').write_text(json.dumps(receipt))
    report = {'status': 'passed', 'selection': 'cdec-fnf', 'stations': [dict(entry, task_index=0)]}
    path = work/'acceptance.json'
    path.write_text(json.dumps(report))
    result = mod.update_evidence(tmp_path, path)
    assert result[entry['path']]['receipt']['retrieved_utc'] == receipt['retrieved_utc']
    receipt['result']['entry']['sha256'] = 'wrong'
    (work/'0/publication.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='mismatch'):
        mod.update_evidence(tmp_path, path)
