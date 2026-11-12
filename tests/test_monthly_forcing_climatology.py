import json
import subprocess
import sys
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from hydro_ops.forcing.monthly_climatology import LEVELS, METHOD, quantiles


def test_exact_counts_missing_zeros_and_blocks():
    data = np.arange(46*2*3, dtype=float).reshape(46, 2, 3)
    data[:, 0, 0] = 0
    data[:5, 0, 1] = np.nan
    data[:, 1, 1] = np.nan
    q, count = quantiles(data, block_cells=2)
    assert count[0, 1] == 41
    assert count[1, 1] == 0
    assert np.isnan(q[:, 1, 1]).all()
    assert (q[:, 0, 0] == 0).all()
    for y, x in [(0, 0), (0, 1), (0, 2), (1, 0), (1, 2)]:
        np.testing.assert_allclose(q[:, y, x], np.nanpercentile(
            data[:, y, x], LEVELS, method=METHOD), rtol=1e-6)
    assert np.isnan(quantiles(data, min_samples=46)[0][:, 0, 1]).all()


def test_bad_arguments():
    with pytest.raises(ValueError):
        quantiles(np.zeros((2, 3)))


def test_end_to_end(tmp_path):
    root = tmp_path / 'forcing/outputs/cnrfc/retro/monthly'
    for year in range(1979, 2026):
        for month in range(1, 13):
            key = year*100 + month
            if not 197910 <= key <= 202509:
                continue
            path = root / str(year) / f'{key}.LDASIN_DOMAIN1.monthly'
            path.parent.mkdir(parents=True, exist_ok=True)
            with netCDF4.Dataset(path, 'w') as ds:
                for name, size in [('time', 1), ('y', 1), ('x', 2), ('nv4', 4), ('bounds', 2)]:
                    ds.createDimension(name, size)
                for name in ['lat', 'lon', 'subset_model_mask', 'subset_forcing_mask']:
                    ds.createVariable(name, 'f4', ('y', 'x'))[:] = 1
                for name in ['lat_bnds', 'lon_bnds']:
                    ds.createVariable(name, 'f4', ('y', 'x', 'nv4'))[:] = 1
                t = ds.createVariable('time', 'f8', ('time',))
                t.bounds = 'time_bounds'
                b = ds.createVariable('time_bounds', 'f8', ('time', 'bounds'))
                b.units = f'days since {year}-{month:02d}-01 00:00:00'
                import calendar
                b[:] = [[0, calendar.monthrange(year, month)[1]]]
                for name, unit in [('T2D', 'K'), ('RAINRATE', 'kg/m^2/s')]:
                    v = ds.createVariable(name, 'f4', ('time', 'y', 'x'), fill_value=-9999.)
                    v.units = unit
                    v[:] = [[[year - 1900, -9999.]]]
            s = path.stat()
            Path(str(path) + '.subset.json').write_text(json.dumps({
                'status': 'passed', 'output_identity': [s.st_ino, s.st_size, s.st_mtime_ns]}))
    output = tmp_path / 'result'
    subprocess.run([sys.executable, 'bin/produce_monthly_forcing_climatology.py',
                    '--domain', 'cnrfc', '--project-root', str(tmp_path),
                    '--output-dir', str(output)], check=True, capture_output=True)
    assert json.loads((output / 'report.json').read_text())['status'] == 'passed'
    with netCDF4.Dataset(output / 'monthly_forcing_percentiles.nc') as ds:
        assert ds['T2D'].shape == (12, 100, 1, 2)
        assert (ds['T2D_sample_count'][:, 0, 0] == 46).all()
        assert ds['RAINRATE'][:, :, 0, 1].mask.all()
