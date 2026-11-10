import numpy as np
import pytest
from netCDF4 import Dataset
from test_nwm_forcing_coordinator import load_script

from hydro_ops.forcing.physics import adjust_temperature_range


@pytest.mark.parametrize('values', [[280., 280.1], [270., 290.], [280., 281.]])
def test_midpoint_survives_small_range_and_clipped_scale(values):
    result = adjust_temperature_range(np.array(values)[:, None], [275.], [285.])
    assert (result.temperature.min()+result.temperature.max())/2 == pytest.approx(280.)


def test_statistics_excludes_missing_and_mask():
    module = load_script('audit_prism_temperature_midpoint.py')
    result = module.statistics(np.array([1., 2., np.nan, 99.]), np.array([1., 1., 1., 0.]),
                               np.array([True, True, True, False]))
    assert result['cells'] == 2
    assert result['bias_k'] == .5
    assert result['cells_above_0_1k'] == 1


@pytest.mark.parametrize('missing', [False, True])
def test_exact_prism_window_uses_previous_noon_through_current_11(tmp_path, missing):
    from datetime import date
    module = load_script('audit_prism_temperature_midpoint.py')
    for day in (1, 2):
        path = tmp_path/f'forcing/outputs/conus/retro/hourly/2026/03/2026030{day}.LDASIN_DOMAIN1'
        path.parent.mkdir(parents=True, exist_ok=True)
        with Dataset(path, 'w') as ds:
            ds.createDimension('time', 24)
            ds.createDimension('y', 2)
            ds.createDimension('x', 2)
            t = ds.createVariable('time', 'f8', ('time',))
            t.units = 'hours since 2026-03-01 00:00:00'
            t[:] = np.arange(24)+(day-1)*24
            if missing and day == 2:
                t[0] = 100
            v = ds.createVariable('T2D', 'f4', ('time', 'y', 'x'))
            v[:] = np.broadcast_to((np.arange(24)+(day-1)*24)[:, None, None], (24, 2, 2))
    if missing:
        with pytest.raises(ValueError, match='Incomplete'):
            module.extrema(tmp_path, 'retro', date(2026, 3, 2), (2, 2))
    else:
        midpoint, donor_free, pins = module.extrema(tmp_path, 'retro', date(2026, 3, 2), (2, 2))
        np.testing.assert_array_equal(midpoint, np.full((2, 2), (12+35)/2))
        assert not donor_free.any()
        assert len(pins) == 2
