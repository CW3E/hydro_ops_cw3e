from datetime import datetime, timedelta

import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.forcing.nrt_rain_timing import load_optional_timing
from hydro_ops.forcing.prism_safeguards import allocate_daily_depth


def make_file(path, hours, units='kg m-2'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, 'w') as ds:
        for name, size in [('time', len(hours)), ('lat', 1), ('lon', 1)]:
            ds.createDimension(name, size)
        t = ds.createVariable('time', 'f8', ('time',))
        t.units = 'hours since 2026-08-18 00:00:00'
        t[:] = hours
        ds.createVariable('lon', 'f8', ('lon',))[:] = [-120]
        ds.createVariable('lat', 'f8', ('lat',))[:] = [40]
        rain = ds.createVariable('Rainf', 'f8', ('time', 'lat', 'lon'))
        rain.units = units
        rain[:] = 1


@pytest.mark.parametrize('layout', ['daily', 'hourly', 'partial', 'missing'])
def test_layout_and_missing(tmp_path, layout):
    daily = tmp_path/'2026/NLDAS_FORA0125_H.A20260818.020.nc'
    stamps = [datetime(2026, 8, 18)+timedelta(hours=i) for i in range(24)]  # noqa: DTZ001
    if layout == 'daily':
        make_file(daily, list(range(24)))
    elif layout in ('hourly', 'partial'):
        for hour in range(24 if layout == 'hourly' else 13):
            make_file(tmp_path/f'2026/230/NLDAS_FORA0125_H.A20260818.{hour:02d}00.020.nc',
                      [hour])
    timing, report = load_optional_timing([daily]*24, stamps, np.array([-120]), np.array([40]))
    complete = layout in ('daily', 'hourly')
    assert np.isfinite(timing).sum() == (24 if complete else 13 if layout == 'partial' else 0)
    rain, code = allocate_daily_depth(np.array([24.]), np.zeros((24, 1)), timing)
    assert code[0] == (2 if complete else 3)
    np.testing.assert_allclose(rain.sum(axis=0), [24])
    assert bool(report['used_files']) == (layout != 'missing')
    _, code = allocate_daily_depth(np.array([24.]), np.ones((24, 1)), timing)
    assert code[0] == 1


def test_bad_units_fail(tmp_path):
    daily = tmp_path/'2026/NLDAS_FORA0125_H.A20260818.020.nc'
    make_file(daily, [0], units='wrong')
    with pytest.raises(ValueError, match='units'):
        load_optional_timing([daily], [datetime(2026, 8, 18)], np.array([-120]), np.array([40]))  # noqa: DTZ001
