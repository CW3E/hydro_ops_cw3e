import importlib.util
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
from test_station_updates import sample

from hydro_ops.observations.station_catalog import read_catalog, write_catalog
from hydro_ops.observations.station_store import write_station

spec = importlib.util.spec_from_file_location('converted_updates', Path(__file__).parents[1]/'bin/test_converted_station_updates.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_complete_month_windows_and_swe_season():
    end = date(2026, 11, 7)
    assert module.windows({'resolution': 'monthly', 'product': 'full_natural_flow'}, end) == [
        ('2026-09-01', '2026-10-31')]
    assert module.windows({'resolution': 'monthly', 'product': 'snow_water_equivalent'}, end) == [
        ('2026-03-01', '2026-05-31'), ('2026-09-01', '2026-10-31')]
    assert module.windows({'resolution': 'daily'}, end) == [('2026-10-20', '2026-11-07')]
    assert module.windows({'resolution': 'monthly', 'product': 'flow'}, date(2024, 3, 4)) == [
        ('2024-01-01', '2024-02-29')]


def test_representative_selection_keeps_sensor_and_duration_separate():
    entries = [{'station_id': station, 'provider': 'cdec', 'product': 'swe',
                'sensor': sensor, 'resolution': resolution}
               for station in ('BBB', 'AAA') for sensor in ('3', '82') for resolution in ('daily', 'monthly')]
    selected = module.select_entries(entries)
    assert len(selected) == 4
    assert {e['station_id'] for e in selected} == {'AAA'}


def test_collection_handoff_and_tamper_rejection(tmp_path, monkeypatch):
    base = tmp_path/'observations/work/station-migration'
    source, catalog = base/'source.nc', base/'collection.json'
    write_station(source, sample(), lock_root=base/'locks')
    write_catalog(catalog, [source], lock_root=base/'locks')
    receipt = catalog.with_suffix('.acceptance.json')
    receipt.write_text(json.dumps({'status': 'passed', 'catalog_sha256': module.digest(catalog),
                                  'catalog': str(catalog.relative_to(tmp_path)), 'members': 1}))
    _, entries, _ = module.accepted_collection(tmp_path, catalog)
    incoming = deepcopy(sample())
    incoming.rows[-1]['value'] = '99'
    monkeypatch.setattr(module, 'acquire_window', lambda *a, **k: (incoming, {}))
    monkeypatch.setattr(module, 'windows', lambda *a: [('2020-01-01', '2020-01-03')])
    result = module.exercise(tmp_path, source, tmp_path/'observations/work/handoff/0/station.nc',
                             entries[0], date(2020, 1, 3), source_catalog=catalog)
    assert result['collection_reader_verified'] and result['original_unchanged']
    assert result['windows'][0]['revised'] == 1
    assert result['windows'][0]['repeat'] == 'unchanged'
    assert read_catalog(catalog, verify=True)['entries'] == entries
    catalog.write_text(catalog.read_text()+'\n')
    with pytest.raises(ValueError, match='checksum mismatch'):
        module.accepted_collection(tmp_path, catalog)
