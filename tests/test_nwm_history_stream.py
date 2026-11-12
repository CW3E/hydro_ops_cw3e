"""Small-grid streaming writer checks, with no production archive dependency."""
import importlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


@pytest.mark.parametrize('domain', ['cnrfc', 'ucrb'])
def test_corrected_month_gate_rejects_old_evidence(tmp_path, monkeypatch, domain):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('benchmark_nwm_history_stream')
    old = tmp_path/f'nwm/runs/{domain}/retro/hu18_pilot_v1/198101'
    old.mkdir(parents=True)
    (old/'accepted.json').write_text('{"passed": true, "stream": "retro"}')
    with pytest.raises(FileNotFoundError):
        module.accepted_month(tmp_path, '198101', domain)
    corrected = old.parent.parent/module.MODEL_CAMPAIGN/'198101'
    corrected.mkdir(parents=True)
    receipt = corrected/'accepted.json'
    receipt.write_text('{"passed": true, "stream": "retro"}')
    namelist = corrected/'namelist.hrldas'
    namelist.write_text('\n'.join(f'soil_thick_input({i}) = {v}'
                                 for i, v in enumerate(module.THICKNESSES, 1)))
    pins = module.accepted_month(tmp_path, '198101', domain)
    assert len(pins) == 2
    assert all(module.MODEL_CAMPAIGN in p['path'] for p in pins)
    receipt.write_text(json.dumps({'passed': False, 'stream': 'retro'}))
    with pytest.raises(ValueError, match='Unaccepted'):
        module.accepted_month(tmp_path, '198101', domain)
    receipt.write_text('{"passed": true, "stream": "retro"}')
    namelist.write_text('soil_thick_input(1) = 9')
    with pytest.raises(ValueError, match='thicknesses'):
        module.accepted_month(tmp_path, '198101', domain)


def test_stream_derivation_and_partial_buffer(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('benchmark_nwm_history_stream')
    path = tmp_path / 'input.nc'
    active = np.array([[True, False, True], [True, True, True]])
    with Dataset(path, 'w') as ds:
        for dim, size in [('time', 1), ('y', 2), ('x', 3), ('soil_layers_stag', 4), ('bounds', 2)]:
            ds.createDimension(dim, size)
        ds.day_definition = 'model_interval'
        b = ds.createVariable('time_bounds', 'f8', ('time', 'bounds'))
        b.units = 'days since 1980-01-01'; b[:] = [[0, 1]]
        s = ds.createVariable('SOIL_M', 'f4', ('time', 'y', 'soil_layers_stag', 'x'), fill_value=-9999.)
        s.units = 'm3 m-3'; s.cell_methods = 'time: mean'; s[:] = .25
        s[0, 1, 2, 2] = -9999.
        v = ds.createVariable('SNEQV', 'f4', ('time', 'y', 'x'))
        v.units = 'kg m-2'; v.cell_methods = 'time: mean'; v[:] = 5.
        v[0, 1, 0] = -1.
        result = module.derive(ds, date(1980, 1, 1), active)
        assert result[0, 0, 0] == 500.
        assert np.isnan(result[:, 0, 1]).all()
        assert np.isnan(result[0, 1, 2])
        assert np.isnan(result[1, 1, 0])
        with pytest.raises(ValueError, match='bounds'):
            module.derive(ds, date(1980, 1, 2), active)
    tile = {'window': [0, 2, 2, 3]}
    with Dataset(tmp_path / 'tile.nc', 'w') as ds:
        for dim, size in [('time', 3), ('y', 2), ('x', 1)]:
            ds.createDimension(dim, size)
        for name in module.FIELDS:
            ds.createVariable(name, 'f4', ('time', 'y', 'x'), fill_value=-9999.)
        module.append_buffer([ds], [tile], np.stack([result]*2), 0)
        module.append_buffer([ds], [tile], result[None], 2)
        for j, name in enumerate(module.FIELDS):
            np.testing.assert_allclose(ds[name][:].filled(np.nan),
                                       np.stack([result[j, :, 2:3]]*3), equal_nan=True)
