from datetime import date

import numpy as np
import pytest
from netCDF4 import Dataset
from test_rain_repair_writer import make

from hydro_ops.forcing.rain_envelope_writer import mask_values, write_enveloped_day
from hydro_ops.forcing.rain_repair_writer import digest, verify_unchanged


def test_envelope_assembly_retains_values_and_originals(tmp_path):
    original, window = tmp_path/'original', tmp_path/'window'
    make(original)
    make(window, 2, True)
    before = digest(window)
    keep = np.array([[True, True], [False, False]])
    active = np.array([[True, False], [False, False]])
    result = write_enveloped_day(original, [window]*24, list(range(24)), tmp_path/'output',
                                 tmp_path/'scratch', date(2014, 1, 5), active, keep, {})
    assert digest(window) == before
    assert result['envelope_assembly']['retained_window_rain_exact']
    assert result['envelope_assembly']['removed_window_cell_hours'][str(window)] == 48
    assert verify_unchanged(original, tmp_path/'output') == 168
    with Dataset(tmp_path/'output') as ds:
        rain = ds['RAINRATE'][:]
        assert np.ma.getmaskarray(rain)[:, ~keep].all()
        np.testing.assert_array_equal(rain[:, keep], 2)
        np.testing.assert_array_equal(ds['prism_timing_source_id'][:][:, ~keep], 0)


def test_retained_bad_values_cannot_be_hidden():
    mask = np.ones((1, 1), bool)
    for value in (np.nan, -1):
        with pytest.raises(ValueError, match='Invalid retained'):
            mask_values(np.array([[value]]), mask, mask)
    with pytest.raises(ValueError, match='Inconsistent'):
        mask_values(np.ones((1, 1)), mask, ~mask)
