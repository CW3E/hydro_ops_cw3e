import importlib.util
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def test_full_schema_retains_met_and_invalidates_old_rain_diagnostics(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'bin/trial_prism_full_schema.py'
    spec = importlib.util.spec_from_file_location('full_trial', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original, rain, result = [tmp_path / p for p in ('original.nc', 'rain.nc', 'result.nc')]
    for destination in (original, rain):
        with Dataset(destination, 'w') as ds:
            for name, size in [('time', 24), ('y', 2), ('x', 2)]:
                ds.createDimension(name, size)
            t = ds.createVariable('time', 'i8', ('time',))
            t.units = 'hours since 2002-01-15 00:00:00'
            t[:] = np.arange(24)
            names = module.FIELDS + ['precip_qc_flags'] if destination == original else ['RAINRATE', 'timing_source']
            for name in names:
                v = ds.createVariable(name, 'f4', ('time', 'y', 'x'), zlib=True, fill_value=-9999.)
                v[:] = 1 if destination == original else 2
            if destination == original:
                ds.createVariable('subset_model_mask', 'u1', ('y', 'x'))[:] = 1
                ds.prism_reconciliation_accepted = 'true'
    report = module.integrate(original, rain, result)
    assert report['nonrain_values_unchanged']
    with Dataset(result) as ds:
        assert 'prism_reconciliation_accepted' not in ds.ncattrs()
        assert ds.historical_prism_reconciliation_accepted == 'true'
        assert 'historical_precip_qc_flags' in ds.variables
        np.testing.assert_array_equal(ds['T2D'][:], 1)
        np.testing.assert_array_equal(ds['RAINRATE'][:], 2)
