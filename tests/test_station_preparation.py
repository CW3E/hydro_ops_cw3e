from copy import deepcopy

import pytest
from test_station_operations import bootstrap
from test_station_updates import sample

from hydro_ops.observations import station_operations as ops
from hydro_ops.observations.station_catalog import read_catalog
from hydro_ops.observations.station_preparation import (
    PreparedPublisher,
    prepare_window,
    publish_prepared,
)
from hydro_ops.observations.station_store import read_station, write_station
from hydro_ops.observations.station_updates import merge_window


def prepared(root, changed=True):
    bootstrap(root)
    entry = read_catalog(ops.active_catalog(root))['entries'][0]
    incoming = deepcopy(sample())
    if changed:
        incoming.rows[-1]['value'] = '99'
    path = root/'incoming.nc'
    write_station(path, incoming, lock_root=root/'locks')
    result = prepare_window(root, entry, path, '2020-01-01', '2020-01-03',
                            root/'observations/work/station-cycles/test/0')
    return entry, incoming, result


@pytest.mark.parametrize('changed', [False, True])
def test_preparation_does_not_publish_and_matches_reference(tmp_path, changed):
    root, reference = tmp_path/'new', tmp_path/'reference'
    entry, incoming, result = prepared(root, changed)
    assert read_catalog(ops.active_catalog(root))['entries'][0] == entry
    bootstrap(reference)
    ref_entry = read_catalog(ops.active_catalog(reference))['entries'][0]
    with ops.operation_lock(reference):
        expected = ops.publish_window(reference, ref_entry, incoming, '2020-01-01', '2020-01-03',
                                      reference/'observations/work/station-cycles/test/0')
    with ops.operation_lock(root):
        actual = publish_prepared(root, result)
    assert actual['status'] == expected['status']
    assert read_station(ops.active_catalog(root).parent/entry['path']) == read_station(
        ops.active_catalog(reference).parent/ref_entry['path'])
    assert actual['entry']['content_sha256'] == expected['entry']['content_sha256']


@pytest.mark.parametrize('change', ['source', 'candidate', 'identity'])
def test_stale_or_corrupt_preparation_rejected(tmp_path, change):
    entry, _, result = prepared(tmp_path)
    if change == 'source':
        (ops.active_catalog(tmp_path).parent/entry['path']).write_bytes(b'changed')
    elif change == 'candidate':
        from pathlib import Path
        Path(result['candidate']).write_bytes(b'changed')
    else:
        result['entry']['station_id'] = 'wrong'
    with ops.operation_lock(tmp_path), pytest.raises(ValueError):
        publish_prepared(tmp_path, result)


@pytest.mark.parametrize('after_copy', [False, True])
def test_prepared_publication_recovers_interruption(tmp_path, monkeypatch, after_copy):
    entry, incoming, result = prepared(tmp_path)
    copy = ops.copy_atomic
    def fail(*args):
        if after_copy:
            copy(*args)
        raise OSError('interruption')
    with ops.operation_lock(tmp_path):
        monkeypatch.setattr(ops, 'copy_atomic', fail)
        with pytest.raises(OSError, match='interruption'):
            publish_prepared(tmp_path, result)
        monkeypatch.setattr(ops, 'copy_atomic', copy)
        ops.recover_pending(tmp_path)
    assert read_catalog(ops.active_catalog(tmp_path), verify=True)
    assert read_station(ops.active_catalog(tmp_path).parent/entry['path']) == merge_window(
        sample(), incoming, start='2020-01-01', end='2020-01-03')


def test_catalog_drift_and_poisoned_coordinator(tmp_path):
    _, _, result = prepared(tmp_path)
    with ops.operation_lock(tmp_path):
        publisher = PreparedPublisher(tmp_path)
        catalog = ops.active_catalog(tmp_path)
        catalog.write_text(catalog.read_text()+'\n')
        with pytest.raises(ValueError, match='outside coordinator'):
            publisher.publish(result)
        with pytest.raises(ValueError, match='recover'):
            publisher.publish(result)


@pytest.mark.parametrize('after_write', [False, True])
def test_catalog_interruption_recoverable(tmp_path, monkeypatch, after_write):
    import hydro_ops.observations.station_preparation as prep
    entry, incoming, result = prepared(tmp_path)
    original = prep.atomic_json
    catalog = ops.active_catalog(tmp_path)
    def interrupt(path, value):
        if path == catalog:
            if after_write:
                original(path, value)
            raise OSError('catalog interruption')
        original(path, value)
    with ops.operation_lock(tmp_path):
        monkeypatch.setattr(prep, 'atomic_json', interrupt)
        with pytest.raises(OSError, match='catalog interruption'):
            publish_prepared(tmp_path, result)
        ops.recover_pending(tmp_path)
    assert read_catalog(catalog, verify=True)
    assert read_station(catalog.parent/entry['path']) == merge_window(
        sample(), incoming, start='2020-01-01', end='2020-01-03')
def test_coordinator_reuses_catalog_and_rejects_duplicate_old_candidate(tmp_path, monkeypatch):
    _, _, result = prepared(tmp_path)
    with ops.operation_lock(tmp_path):
        publisher = PreparedPublisher(tmp_path)
        def unexpected(*args, **kwargs):
            raise AssertionError('Catalog should not be rescanned')
        monkeypatch.setattr('hydro_ops.observations.station_preparation.read_catalog', unexpected)
        assert publisher.publish(result)['status'] == 'published'
        with pytest.raises(ValueError, match='stale'):
            publisher.publish(result)
