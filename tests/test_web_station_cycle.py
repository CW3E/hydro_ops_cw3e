from types import SimpleNamespace

import pytest

from hydro_ops.web import station_cycle as mod
from hydro_ops.web import station_fnf
from hydro_ops.web.globus_transfer import atomic_json, digest


@pytest.fixture
def history(tmp_path, monkeypatch):
    root = tmp_path
    base = root/'observations/processed/stations/v1'
    base.mkdir(parents=True)
    (root/'config').mkdir()
    atomic_json(root/'config/cdec_fnf_stations.json', {'station_ids': ['ABC']})
    tables, entries = {}, []
    for resolution in ('daily', 'monthly'):
        sensor, duration, native, factor, unit = station_fnf.PRODUCTS[resolution]
        name = f'cdec/ABC/full_natural_flow/{sensor}/{resolution}.nc'
        path = base/name
        path.parent.mkdir(parents=True)
        path.write_text(resolution)
        row = {'station_id': 'ABC', 'sensor_number': str(sensor), 'duration_code': duration,
               'timestamp_pst': '2026-11-01T00:00:00-08:00',
               'period_label': '2026-11-01' if resolution == 'daily' else '2026-11',
               'raw_value': '1', 'value': '1', 'value_si': str(factor), 'units': native,
               'units_si': unit, 'value_status': 'numeric', 'data_flag': ' '}
        meta = {'provider': 'cdec', 'station_id': 'ABC', 'product': 'full_natural_flow',
                'sensor': str(sensor), 'resolution': resolution, 'units_si': unit,
                'auxiliary': {'station.json': {'longitude': -120., 'latitude': 38., 'station_name': 'ABC',
                    'river_basin': 'Test', 'elevation_raw': '1 ft', 'source_url': 'https://example.org/',
                    'historical_notes': []}}}
        tables[path] = SimpleNamespace(rows=[row], fields=list(row), metadata=meta)
        entries.append({'provider': 'cdec', 'station_id': 'ABC', 'product': 'full_natural_flow',
                        'sensor': str(sensor), 'resolution': resolution, 'path': name,
                        'sha256': digest(path), 'rows': 1})
    atomic_json(base/'catalog.json', {'entries': entries})
    monkeypatch.setattr(station_fnf, 'read_station', tables.__getitem__)
    monkeypatch.setattr(mod, 'read_station', tables.__getitem__)
    old = root/'web/publications/observations/old'
    station_fnf.build(root, old)
    accepted = {'release': str(old.relative_to(root)), 'pins': mod.pins_from_release(root, old, 'cdec-fnf'),
                'version': mod.VERSION}
    report_path = root/'observations/work/station-cycles/123/acceptance.json'
    report_path.parent.mkdir(parents=True)
    report = {'status': 'passed', 'selection': 'cdec-fnf', 'stations': [
        {'path': e['path'], 'sha256': e['sha256'], 'status': 'unchanged'} for e in entries]}
    atomic_json(report_path, report)
    return root, base, old, tables, entries, accepted, report_path, report


def test_no_change_and_one_station_revision(history):
    root, base, old, tables, entries, accepted, path, report = history
    output = old.with_name('new')
    assert mod.render(root, 'cdec-fnf', path, accepted, output)['status'] == 'unchanged'
    assert not output.exists()
    source = base/entries[0]['path']
    source.write_text('revised')
    entries[0]['sha256'] = digest(source)
    tables[source].rows[0].update(value='2', raw_value='2', value_si=str(2*station_fnf.PRODUCTS['daily'][3]))
    atomic_json(base/'catalog.json', {'entries': entries})
    report['stations'][0].update(status='published', sha256=entries[0]['sha256'])
    atomic_json(path, report)
    before = digest(old/'daily/data/ABC/sensor-8/history.csv.gz')
    result = mod.render(root, 'cdec-fnf', path, accepted, output)
    assert result['changed'] == [entries[0]['path']]
    assert digest(old/'daily/data/ABC/sensor-8/history.csv.gz') == before
    assert digest(output/'daily/data/ABC/sensor-8/history.csv.gz') != before
    assert (output/'monthly/data/ABC/sensor-65/history.csv.gz').stat().st_ino == (
        old/'monthly/data/ABC/sensor-65/history.csv.gz').stat().st_ino
    mod.station_inventory(output)


def test_partial_failure_retains_and_marks_stale(history):
    root, _, old, _, entries, accepted, path, report = history
    report['status'] = 'partial'
    report['stations'][0] = {'path': entries[0]['path'], 'status': 'failed'}
    atomic_json(path, report)
    output = old.with_name('partial')
    result = mod.render(root, 'cdec-fnf', path, accepted, output)
    assert result['stale'] == [entries[0]['path']]
    assert result['changed'] == []
    assert digest(output/'daily/data/ABC/sensor-8/history.csv.gz') == digest(old/'daily/data/ABC/sensor-8/history.csv.gz')
    assert mod.read(output/'daily/collection.json')['assets'][0]['updateStatus'] == 'stale-retained'


def test_metadata_refresh_reuses_all_payloads(history):
    root, _, old, _, _, accepted, path, _ = history
    output = old.with_name('metadata')
    result = mod.render(root, 'cdec-fnf', path, accepted, output, refresh_metadata=True)
    assert result['changed'] == result['stale'] == []
    for row in mod.station_inventory(output)['files']:
        if row['role'] == 'payload':
            assert digest(old/row['path']) == row['sha256']
            assert (old/row['path']).stat().st_ino == (output/row['path']).stat().st_ino


def test_bad_station_export_is_isolated(history):
    root, base, old, tables, entries, accepted, path, report = history
    source = base/entries[0]['path']
    source.write_text('invalid')
    entries[0]['sha256'] = digest(source)
    tables[source].rows[0]['value_si'] = 'nan'
    atomic_json(base/'catalog.json', {'entries': entries})
    report['stations'][0]['sha256'] = entries[0]['sha256']
    atomic_json(path, report)
    output = old.with_name('retained')
    result = mod.render(root, 'cdec-fnf', path, accepted, output)
    assert result['export_errors'] and result['changed'] == []
    assert result['pins'][entries[0]['path']]['sha256'] == accepted['pins'][entries[0]['path']]['sha256']
    assert digest(output/'daily/data/ABC/sensor-8/history.csv.gz') == digest(old/'daily/data/ABC/sensor-8/history.csv.gz')


def test_new_membership_requires_review(history):
    root, _, old, _, _, accepted, path, _ = history
    atomic_json(root/'config/cdec_fnf_stations.json', {'station_ids': ['ABC', 'NEW']})
    with pytest.raises(ValueError, match='membership'):
        mod.render(root, 'cdec-fnf', path, accepted, old.with_name('bad'))


def test_schedule_afterany_and_idempotence(tmp_path, monkeypatch):
    from hydro_ops.web import station_schedule
    (tmp_path/'config').mkdir()
    atomic_json(tmp_path/'config/web_stations.json', {'products': {'cdec-fnf': {'enabled': True, 'mode': 'publish'}}})
    commands = []
    def submit(command, **kwargs):
        commands.append(command)
        assert 'SBATCH_RESERVATION' not in kwargs['env']
        return '456\n'
    monkeypatch.setattr(station_schedule.subprocess, 'check_output', submit)
    monkeypatch.setenv('SBATCH_RESERVATION', 'NRTRES')
    record = station_schedule.submit(tmp_path, 'cdec-fnf', {'status': 'submitted', 'job_id': '123'})
    assert record['job_id'] == '456'
    assert '--dependency=afterany:123' in commands[0]
    assert 'observations/work/station-cycles/123/acceptance.json' in commands[0]
    assert '--publish' in commands[0]
    assert station_schedule.submit(tmp_path, 'cdec-fnf', {'status': 'submitted', 'job_id': '123'})['status'] == 'already_recorded'
    assert len(commands) == 1


def test_pending_resume_does_not_rebuild(history, monkeypatch):
    root, _, old, _, _, accepted, path, _ = history
    config = {'destination_collection_id': 'test', 'destination_root': '/web',
              'public_base_url': 'https://example.org/', 'production_enabled': True}
    monkeypatch.setattr(mod, 'configuration', lambda *a: ({'enabled': False, 'mode': 'publish'}, config))
    control = root/'web/status/stations/cdec-fnf'
    control.mkdir(parents=True)
    atomic_json(control/'accepted.json', accepted)
    pending = {'config': config, 'release': str(old.relative_to(root)), 'deployment': 'test-plan'}
    atomic_json(control/'pending.json', pending)
    monkeypatch.setattr(mod, 'render', lambda *a: pytest.fail('must resume existing prepared publication'))
    assert mod.run(root, 'cdec-fnf', path)['status'] == 'prepared_not_published'
    with pytest.raises(ValueError, match='disabled'):
        mod.run(root, 'cdec-fnf', path, publish=True)
