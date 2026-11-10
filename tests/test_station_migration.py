import json

import pytest

from hydro_ops.observations.station_migration import discover, inspect, inventory


def fnf(root, station='AAA', resolution='daily', status='passed', data=True):
    base = root/'observations/processed/cdec/fnf'/resolution/station
    base.mkdir(parents=True)
    (base/'manifest.json').write_text(json.dumps({'status': status, 'station_id': station,
                                                'resolution': resolution}))
    if data:
        (base/'2020.csv').write_text('example payload,not read by metadata screening\n')
    return base


def test_inventory_is_screening_not_acceptance(tmp_path):
    base = fnf(tmp_path)
    before = (base/'2020.csv').read_bytes()
    fnf(tmp_path, 'BAD', status='failed')
    fnf(tmp_path, 'EMPTY', data=False)
    report = inventory(tmp_path, pilot_limit=2)
    assert report['counts'] == {'candidate': 1, 'blocked': 1, 'empty_or_missing': 1}
    assert not report['payload_checksums_verified']
    assert not report['proposed_pilot']['execute']
    assert len(report['proposed_pilot']['task_ids']) == 1
    assert (base/'2020.csv').read_bytes() == before
    assert report == inventory(tmp_path, pilot_limit=2)


def test_balanced_bounded_selection(tmp_path):
    for station in ('AAA', 'BBB', 'CCC'):
        fnf(tmp_path, station)
    fnf(tmp_path, 'AAA', resolution='monthly')
    report = inventory(tmp_path, pilot_limit=2)
    chosen = [e for e in report['entries'] if e['task_id'] in report['proposed_pilot']['task_ids']]
    assert {e['resolution'] for e in chosen} == {'daily', 'monthly'}
    with pytest.raises(ValueError):
        inventory(tmp_path, pilot_limit=0)


def test_missing_receipt_and_source_identity_change(tmp_path):
    base = fnf(tmp_path)
    entry = next(iter(discover(tmp_path)))
    first = inspect(tmp_path, entry)
    (base/'2020.csv').write_text('changed payload')
    second = inspect(tmp_path, entry)
    assert first['data_files'] != second['data_files']
    (base/'manifest.json').unlink()
    assert inspect(tmp_path, entry)['status'] == 'blocked'
