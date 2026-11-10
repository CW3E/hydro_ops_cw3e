import json

import pytest
from netCDF4 import Dataset
from test_rain_repair_endpoints import module


def summary(path, rows, monthly=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, 'w') as ds:
        ds.createDimension('time', 1)
        ds.createDimension('x', 2)
        ds.createVariable('RAINRATE', 'f4', ('time', 'x'), zlib=True)[:] = [[1, 2]]
        ds.source_files = json.dumps(rows)
        ds.aggregation_signature = 'old'
        ds.aggregation_algorithm = 'test'
        ds.interval_start_utc = '1979-01-02T00:00:00Z'
        ds.interval_end_utc = '1979-01-03T00:00:00Z'
        ds.input_resolution = 'daily' if monthly else 'hourly'
        ds.reducer_spec = '{}'
        ds.domain = 'conus'
        ds.forcing_stream = 'retro'


def test_metadata_repair_and_monthly_proof_and_retry(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'repair_forcing_lineage')
    monkeypatch.setattr(mod, 'audited', lambda p: None)
    parent = tmp_path/'retro'
    source = parent/'hourly/1979/01/19790102.LDASIN_DOMAIN1'
    source.parent.mkdir(parents=True)
    source.write_text('unchanged source')
    row = {'path': str(parent/'1979/01'/source.name), 'bytes': source.stat().st_size,
           'mtime_ns': source.stat().st_mtime_ns}
    daily = parent/'daily/1979/01/19790102.LDASIN_DOMAIN1.daily'
    summary(daily, [row])
    daily_row = {'path': str(daily), 'bytes': daily.stat().st_size, 'mtime_ns': daily.stat().st_mtime_ns}
    monthly = parent/'monthly/1979/197901.LDASIN_DOMAIN1.monthly'
    summary(monthly, [daily_row], True)
    work = tmp_path/'repair'
    assert mod.repair_metadata((daily, parent, work))['status'] == 'published'
    assert mod.repair_metadata((daily, parent, work))['status'] == 'unchanged'
    assert mod.repair_metadata((monthly, parent, work))['status'] == 'published'
    with Dataset(daily) as ds:
        assert json.loads(ds.source_files)[0]['path'] == str(source)
        assert ds['RAINRATE'][:].tolist() == [[1, 2]]
    with Dataset(monthly) as ds:
        assert json.loads(ds.source_files)[0]['mtime_ns'] == daily.stat().st_mtime_ns
    source.write_text('changed source data')
    with pytest.raises(ValueError, match='identity changed'):
        mod.repair_metadata((daily, parent, work))


def test_unknown_source_change_rejected(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'repair_forcing_lineage')
    parent = tmp_path/'retro'
    source = parent/'hourly/1979/01/19790102.LDASIN_DOMAIN1'
    source.parent.mkdir(parents=True)
    source.write_text('new')
    path = parent/'daily/1979/01/19790102.LDASIN_DOMAIN1.daily'
    summary(path, [{'path': str(parent/'1979/01'/source.name), 'bytes': 999, 'mtime_ns': 1}])
    with pytest.raises(FileNotFoundError):
        mod.repair_metadata((path, parent, tmp_path/'repair'))
    with Dataset(path) as ds:
        assert ds.aggregation_signature == 'old'
