import copy
import fcntl
import hashlib

import pytest
from netCDF4 import Dataset

from hydro_ops.observations.station_store import StationTable, read_station, write_station


def table():
    return StationTable(
        {'provider': 'test', 'station_id': '0012', 'product': 'discharge', 'sensor': '00060',
         'resolution': 'daily', 'units_si': 'm3 s-1', 'timezone': 'unknown',
         'interval_semantics': 'provider date labels, bounds unknown',
         'mapping': {'series': 'series', 'date': 'day', 'value': 'si', 'status': 'status'},
         'provider_metadata': {'notes': 'café\x00 tail  ', 'datum': None}},
        ['series', 'day', 'raw', 'si', 'status', 'flag'],
        [{'series': 'a', 'day': '2020-02-29', 'raw': '-9999', 'si': '', 'status': 'missing', 'flag': ' '},
         {'series': 'b', 'day': '2020-02-29', 'raw': '0.0', 'si': '0', 'status': 'numeric', 'flag': 'é\x00 '},
         {'series': 'b', 'day': '2020-03-01', 'raw': '-2', 'si': '-2', 'status': 'negative', 'flag': 'e'}])


def test_lossless_roundtrip_and_noop(tmp_path):
    path = tmp_path/'station.nc'
    expected = table()
    assert write_station(path, expected, lock_root=tmp_path/'locks')['status'] == 'published'
    assert read_station(path) == expected
    before = path.stat()
    assert write_station(path, expected, lock_root=tmp_path/'locks')['status'] == 'unchanged'
    assert path.stat().st_ino == before.st_ino
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert len(read_station(path, start='2020-03-01').rows) == 1
    with Dataset(path) as ds:
        assert ds['value_si'].filters()['zlib']
        assert ds['value_si'].units == 'm3 s-1'
        assert 'time_bounds' not in ds.variables


def test_revisions_missing_and_history_loss(tmp_path):
    path = tmp_path/'station.nc'
    old = table()
    write_station(path, old, lock_root=tmp_path/'locks')
    revised = copy.deepcopy(old)
    revised.rows[2].update(raw='-9999', si='', status='missing')
    write_station(path, revised, lock_root=tmp_path/'locks')
    assert read_station(path) == revised
    revised.rows.pop()
    before = path.read_bytes()
    with pytest.raises(ValueError, match='History loss'):
        write_station(path, revised, lock_root=tmp_path/'locks')
    assert path.read_bytes() == before


@pytest.mark.parametrize('change', ['duplicate', 'nan', 'identity'])
def test_invalid_updates_rejected(tmp_path, change):
    path = tmp_path/'station.nc'
    original = table()
    write_station(path, original, lock_root=tmp_path/'locks')
    incoming = copy.deepcopy(original)
    if change == 'duplicate':
        incoming.rows.append(incoming.rows[0])
    elif change == 'nan':
        incoming.rows[0]['si'] = 'nan'
    else:
        incoming.metadata['station_id'] = '12'
    with pytest.raises(ValueError):
        write_station(path, incoming, lock_root=tmp_path/'locks')
    assert read_station(path) == original


def test_lock_and_interrupted_replacement(tmp_path, monkeypatch):
    from hydro_ops.observations import station_store
    path = tmp_path/'station.nc'
    locks = tmp_path/'locks'
    original = table()
    write_station(path, original, lock_root=locks)
    lock_path = locks/(hashlib.sha256(str(path.resolve()).encode()).hexdigest()+'.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            write_station(path, original, lock_root=locks)
    def fail(*args):
        raise OSError('simulated interruption before rename')
    real_replace = station_store.os.replace
    monkeypatch.setattr(station_store.os, 'replace', fail)
    incoming = copy.deepcopy(original)
    incoming.rows[2]['si'] = '3'
    with pytest.raises(OSError):
        write_station(path, incoming, lock_root=locks)
    assert read_station(path) == original
    assert not list(tmp_path.glob('*.tmp'))
    monkeypatch.setattr(station_store.os, 'replace', real_replace)
    write_station(path, incoming, lock_root=locks)
    assert read_station(path) == incoming


def test_monthly_volume_and_empty_history(tmp_path):
    monthly = table()
    monthly.metadata.update(resolution='monthly', units_si='m3')
    monthly.rows = [dict(monthly.rows[0], day='2020-02')]
    path = tmp_path/'monthly.nc'
    write_station(path, monthly, lock_root=tmp_path/'locks')
    assert read_station(path) == monthly
    monthly.rows = []
    empty = tmp_path/'empty.nc'
    write_station(empty, monthly, lock_root=tmp_path/'locks')
    assert read_station(empty) == monthly


def test_corrupt_normalized_column_rejected(tmp_path):
    path = tmp_path/'station.nc'
    write_station(path, table(), lock_root=tmp_path/'locks')
    with Dataset(path, 'a') as ds:
        ds['value_si'][1] = 500
    with pytest.raises(ValueError, match='Normalized numeric'):
        read_station(path)
