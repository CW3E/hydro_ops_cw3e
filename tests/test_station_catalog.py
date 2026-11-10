import copy
import fcntl
import hashlib
import json

import pytest
from test_station_store import table

from hydro_ops.observations.station_catalog import read_catalog, read_member, write_catalog
from hydro_ops.observations.station_store import write_station


def test_catalog_recovery_after_station_revision(tmp_path):
    path, catalog, locks = tmp_path/'a.nc', tmp_path/'catalog.json', tmp_path/'locks'
    original = table()
    write_station(path, original, lock_root=locks)
    assert write_catalog(catalog, [path], lock_root=locks)['status'] == 'published'
    record = read_catalog(catalog, verify=True)['entries'][0]
    assert record['rows'] == 3 and record['numeric_rows'] == 2 and record['series_count'] == 2
    key = {k: record[k] for k in ('provider', 'station_id', 'product', 'sensor', 'resolution')}
    assert read_member(catalog, **key) == original
    assert write_catalog(catalog, [path], lock_root=locks)['status'] == 'unchanged'
    revised = copy.deepcopy(original)
    revised.rows[2]['si'] = '7'
    write_station(path, revised, lock_root=locks)
    with pytest.raises(ValueError, match='changed'):
        read_member(catalog, **key)
    with pytest.raises(ValueError, match='Stale'):
        read_catalog(catalog, verify=True)
    # Recover a crash between station publication and catalog finalization.
    write_catalog(catalog, [path], lock_root=locks)
    assert read_member(catalog, **key, start='2020-03-01').rows == revised.rows[2:]


def test_catalog_rejects_duplicates_and_unsafe_paths(tmp_path):
    path, other, catalog, locks = tmp_path/'a.nc', tmp_path/'b.nc', tmp_path/'catalog.json', tmp_path/'locks'
    write_station(path, table(), lock_root=locks)
    write_catalog(catalog, [path], lock_root=locks)
    before = catalog.read_bytes()
    with pytest.raises(ValueError, match='members omitted'):
        write_catalog(catalog, [], lock_root=locks)
    assert catalog.read_bytes() == before
    write_station(other, table(), lock_root=locks)
    with pytest.raises(ValueError, match='Duplicate'):
        write_catalog(catalog, [path, other], lock_root=locks)
    assert catalog.read_bytes() == before
    document = json.loads(before)
    document['entries'][0]['path'] = '../outside.nc'
    catalog.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='inside'):
        read_catalog(catalog)


def test_catalog_holds_station_writer_lock(tmp_path):
    path, catalog, locks = tmp_path/'a.nc', tmp_path/'catalog.json', tmp_path/'locks'
    write_station(path, table(), lock_root=locks)
    name = hashlib.sha256(str(path.resolve()).encode()).hexdigest()+'.lock'
    with (locks/name).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            write_catalog(catalog, [path], lock_root=locks)
    assert not catalog.exists()
