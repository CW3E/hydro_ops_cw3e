"""Small-grid streaming writer checks, with no production archive dependency."""
import importlib
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


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
