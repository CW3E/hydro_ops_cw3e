import importlib.util
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def test_staging_normalizes_epoch_and_preserves_hourly_provenance(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'bin/test_prism_calendar_strategy.py'
    spec = importlib.util.spec_from_file_location('calendar_strategy', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for day in (10, 11):
        source, destination = tmp_path / f'{day}.nc', tmp_path / f'{day}-staged.nc'
        with Dataset(source, 'w') as ds:
            for name, length in [('time', 24), ('y', 1), ('x', 1)]:
                ds.createDimension(name, length)
            t = ds.createVariable('time', 'i8', ('time',))
            t.units = f'hours since 2014-12-{day:02d} 12:00:00'
            t[:] = np.arange(24)
            p = ds.createVariable('timing_source', 'u1', ('y', 'x'))
            p[:] = day - 9
        module.stage_window(source, destination)
        assert module.stamps(source) == module.stamps(destination)
        with Dataset(destination) as ds:
            assert ds['time'].units == 'hours since 1970-01-01 00:00:00'
            assert ds['timing_source'].shape == (24, 1, 1)
            np.testing.assert_array_equal(ds['timing_source'][:], day - 9)
