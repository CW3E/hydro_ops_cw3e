from concurrent.futures import Future

import pytest
from test_station_operations import bootstrap
from test_station_updates import sample

from hydro_ops.observations import station_operations as ops
from hydro_ops.observations import station_store as store
from hydro_ops.observations.station_catalog import read_catalog
from hydro_ops.observations.station_scheduling import pop_completed


def test_header_matches_without_reading_observations(tmp_path, monkeypatch):
    bootstrap(tmp_path)
    catalog = ops.active_catalog(tmp_path)
    entry = read_catalog(catalog)['entries'][0]
    full = store.read_station(catalog.parent/entry['path'])
    original = store._get_text
    def metadata_only(group, name):
        assert group.path == '/metadata' and name == 'json'
        return original(group, name)
    monkeypatch.setattr(store, '_get_text', metadata_only)
    header = store.read_station_header(catalog.parent/entry['path'])
    assert header.metadata == full.metadata
    assert header.fields == full.fields
    assert header.rows == []


def test_ready_first_does_not_wait_for_oldest():
    slow, ready, failed = Future(), Future(), Future()
    ready.set_result('accepted')
    failed.set_exception(ValueError('station-local failure'))
    pending = [(slow, 'slow'), (ready, 'ready'), (failed, 'failed')]
    assert pop_completed(pending) == (ready, 'ready')
    future, label = pop_completed(pending)
    assert label == 'failed'
    with pytest.raises(ValueError, match='station-local'):
        future.result()
    assert pending == [(slow, 'slow')]


def test_header_context_cache_normalization_matches_full(tmp_path, monkeypatch):
    import importlib
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    # Provider acquisition consumes metadata/field definitions, never old observations.
    from test_usgs_discharge import feature, spec

    from hydro_ops.download import usgs_discharge
    from hydro_ops.observations.station_updates import acquire_window
    table = sample()
    table.metadata.update(provider='usgs', station_id='USGS-00123456', product='discharge',
                          auxiliary={'station.json': spec()})
    table.fields = usgs_discharge.FIELDS.copy()
    table.metadata['mapping'] = {'series': 'time_series_id', 'date': 'provider_date',
                                 'value': 'discharge_m3_s', 'status': 'value_status'}
    table.rows = []
    monkeypatch.setattr(usgs_discharge, 'daily_pages', lambda *a, **kw: ([feature()], []))
    result, _ = acquire_window(tmp_path, tmp_path/'observations/work/raw', table,
                                start='2020-02-28', end='2020-02-28')
    assert len(result.rows) == 1
    assert result.rows[0]['provider_date'] == '2020-02-28'
    assert importlib.import_module('update_station_collection').read_station_header is store.read_station_header
