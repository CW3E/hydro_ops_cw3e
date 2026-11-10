import json
from copy import deepcopy

import pytest
from test_station_updates import sample

from hydro_ops.observations import station_operations as ops
from hydro_ops.observations.station_catalog import read_catalog, read_member, write_catalog
from hydro_ops.observations.station_store import write_station


def bootstrap(root):
    base = root/'observations/work/station-migration'
    path, catalog = base/'test.nc', base/'collection.json'
    write_station(path, sample(), lock_root=base/'locks')
    write_catalog(catalog, [path], lock_root=base/'locks')
    catalog.with_suffix('.acceptance.json').write_text(json.dumps(
        {'status': 'passed', 'catalog_sha256': ops.digest(catalog), 'members': 1}))
    handoff = root/'handoff.json'
    handoff.write_text(json.dumps({'status': 'passed', 'source_catalog_sha256': ops.digest(catalog),
                                   'source_catalog_unchanged': True, 'production_modified': False}))
    ops.cutover(root, catalog, handoff)
    return catalog, handoff


def test_cutover_retains_original_and_blocks_repeat(tmp_path):
    source, handoff = bootstrap(tmp_path)
    target = ops.active_catalog(tmp_path)
    assert source.is_file() and read_catalog(source, verify=True)
    assert len(read_catalog(target, verify=True)['entries']) == 1
    with pytest.raises(ValueError, match='Existing activation'):
        ops.cutover(tmp_path, source, handoff)


@pytest.mark.parametrize('when', ['before_copy', 'after_copy', 'none'])
def test_transaction_recovery(tmp_path, monkeypatch, when):
    bootstrap(tmp_path)
    catalog = ops.active_catalog(tmp_path)
    entry = read_catalog(catalog)['entries'][0]
    incoming = deepcopy(sample())
    incoming.rows[-1]['value'] = '99'
    copy = ops.copy_atomic
    def interrupted(*args):
        if when == 'after_copy':
            copy(*args)
        raise OSError('injected interruption')
    with ops.operation_lock(tmp_path):
        if when != 'none':
            monkeypatch.setattr(ops, 'copy_atomic', interrupted)
            with pytest.raises(OSError, match='interruption'):
                ops.publish_window(tmp_path, entry, incoming, '2020-01-01', '2020-01-03',
                                   tmp_path/'observations/work/station-cycles/job/0')
            monkeypatch.setattr(ops, 'copy_atomic', copy)
            ops.recover_pending(tmp_path)
        else:
            ops.publish_window(tmp_path, entry, incoming, '2020-01-01', '2020-01-03',
                               tmp_path/'observations/work/station-cycles/job/0')
        new = read_catalog(catalog, verify=True)['entries'][0]
        result = ops.publish_window(tmp_path, new, incoming, '2020-01-01', '2020-01-03',
                                    tmp_path/'observations/work/station-cycles/job/1')
        assert result['status'] == 'unchanged'
    table = read_member(catalog, **{k: new[k] for k in ops.IDENTITY})
    assert table.rows[-1]['value'] == '99'


def test_changed_candidate_rejected(tmp_path, monkeypatch):
    bootstrap(tmp_path)
    entry = read_catalog(ops.active_catalog(tmp_path))['entries'][0]
    incoming = deepcopy(sample())
    incoming.rows[-1]['value'] = '99'
    with ops.operation_lock(tmp_path):
        def interrupted(*args):
            raise OSError('stop')
        monkeypatch.setattr(ops, 'copy_atomic', interrupted)
        work = tmp_path/'observations/work/station-cycles/job/0'
        with pytest.raises(OSError):
            ops.publish_window(tmp_path, entry, incoming, '2020-01-01', '2020-01-03', work)
        (work/'candidate.nc').write_bytes(b'changed')
        with pytest.raises(ValueError, match='candidate changed'):
            ops.recover_pending(tmp_path)


def test_path_safety():
    entry = {k: 'safe' for k in ops.IDENTITY}
    entry['station_id'] = '../escape'
    with pytest.raises(ValueError):
        ops.permanent_name(entry)
