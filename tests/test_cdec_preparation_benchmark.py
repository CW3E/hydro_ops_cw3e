import gzip
import hashlib
import json
from types import SimpleNamespace

import pytest
from test_nwm_forcing_coordinator import load_script

from hydro_ops.download.cdec_swe import FIELDS


def test_cached_cdec_native_labels_and_checksum(tmp_path):
    module = load_script('benchmark_station_preparation.py')
    raw = tmp_path/'raw'
    raw.mkdir()
    payload = json.dumps([{'stationId': 'BKL', 'SENSOR_NUM': 3, 'durCode': 'H',
                          'units': 'INCHES', 'date': '2024-12-31 23:00',
                          'value': 1, 'dataFlag': 'e'}]).encode()
    (raw/'response.json.gz').write_bytes(gzip.compress(payload))
    (raw/'acquisition.json').write_text(json.dumps({
        'start': '2024-12-31', 'end': '2024-12-31',
        'sources': [{'sha256': hashlib.sha256(payload).hexdigest()}]}))
    old = SimpleNamespace(metadata={'provider': 'cdec', 'station_id': 'BKL',
                          'sensor': '3', 'resolution': 'hourly',
                          'product': 'snow_water_equivalent'}, fields=FIELDS)
    result = module.incoming_from_cache(old, tmp_path, '2024-12-31', '2024-12-31')
    assert result.rows[0]['timestamp_pst'] == '2024-12-31T23:00:00-08:00'
    assert result.rows[0]['swe_mm'] == '25.4'
    assert result.rows[0]['data_flag'] == 'e'
    (raw/'response.json.gz').write_bytes(gzip.compress(b'[]'))
    with pytest.raises(ValueError, match='checksum'):
        module.incoming_from_cache(old, tmp_path, '2024-12-31', '2024-12-31')


def test_stratification_and_live_largest_histories():
    module = load_script('benchmark_station_preparation.py')
    live = load_script('update_station_collection.py')
    sources = {f'{s}-{r}-{n}': {'sensor': s, 'resolution': r, 'rows': n}
               for s in ('3', '82') for r in ('daily', 'hourly', 'monthly', 'event')
               for n in (1, 10, 100, 1000)}
    choices = [(i, {'path': path}) for i, path in enumerate(sources)]
    selected = module.select_cached(choices, sources, 32, stratified=True)
    assert len(selected) == 24
    assert sum(sources[row['path']]['rows'] == 1000 for _, row in selected) == 8
    with pytest.raises(ValueError, match='budget'):
        module.select_cached(choices, sources, 8, stratified=True)
    assert len(live.representative_entries(list(sources.values()))) == 8
    assert all(e['rows'] == 1000 for e in live.representative_entries(list(sources.values())))
