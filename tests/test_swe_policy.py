from pathlib import Path

from hydro_ops.observations.swe_policy import filter_swe, load_policy


def test_approved_lists_and_pairs():
    root = Path(__file__).resolve().parents[1]
    allowed, report = load_policy(root)
    assert len(allowed) == 391
    assert report['version'] == 'cdec-confirmed-20261115-r2'
    assert ('BKL', '3', 'monthly') not in allowed
    assert ('BKL', '82', 'daily') in allowed
    assert report['exclusions'][0]['station_id'] == 'BKL'
    for (station, sensor, resolution), method in allowed.items():
        assert (sensor, resolution) == (('3', 'monthly') if method == 'snow_course' else ('82', 'daily'))
    entries = [{'station_id': 'BCH', 'sensor': s, 'resolution': r}
               for s in ('3', '82') for r in ('monthly', 'daily', 'hourly', 'event')]
    selected, report = filter_swe(root, entries)
    assert all((e['station_id'], e['sensor'], e['resolution']) in allowed for e in selected)
    assert not any(e['sensor'] == '3' and e['resolution'] == 'daily' for e in selected)
    assert report['excluded'] + report['selected'] == 8
