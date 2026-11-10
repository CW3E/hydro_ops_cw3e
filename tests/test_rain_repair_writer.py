from datetime import date

import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.forcing.rain_repair_writer import FIELDS, verify_unchanged, write_day


def make(path, rain=1, window=False):
    with Dataset(path, 'w') as ds:
        for name, size in [('time', 24), ('y', 2), ('x', 2)]:
            ds.createDimension(name, size)
        t = ds.createVariable('time', 'i8', ('time',))
        t.units = 'hours since 2014-01-05 00:00:00'
        t[:] = np.arange(24)
        for name in FIELDS:
            v = ds.createVariable(name, 'f4', ('time', 'y', 'x'),
                                  zlib=True, chunksizes=(1, 2, 2), fill_value=-9999.)
            v[:] = rain if name == 'RAINRATE' else 280
        ds.prism_day = 'stale'
        if window:
            ds.prism_reconciliation_accepted = 'true'
            ds.createVariable('prism_timing_source_id', 'u1', ('time', 'y', 'x'))[:] = 1


def test_copy_replace(tmp_path):
    original, window, reference = [tmp_path / n for n in ('original', 'window', 'reference')]
    make(original)
    make(window, 2, True)
    make(reference, 2)
    output = tmp_path / 'out'
    mask = np.ones((2, 2), bool)
    receipt = write_day(original, [window] * 24, list(range(24)), output,
                        tmp_path / 'scratch', date(2014, 1, 5), mask, mask,
                        {'archive_period': '2014-01-05'}, reference=reference)
    assert receipt['reference_equal']
    assert receipt['unchanged_chunks_verified'] == 7 * 24
    with Dataset(output) as ds, Dataset(original) as source:
        assert 'prism_day' not in ds.ncattrs()
        assert ds.prism_reconciliation_accepted == 'true'
        np.testing.assert_array_equal(ds['RAINRATE'][:], 2)
        np.testing.assert_array_equal(source['RAINRATE'][:], 1)
    with pytest.raises(FileExistsError):
        write_day(original, [window] * 24, list(range(24)), output,
                  tmp_path / 'scratch2', date(2014, 1, 5), mask, mask, {})
    with Dataset(output, 'r+') as ds:
        ds['T2D'][0, 0, 0] = 279
    with pytest.raises(ValueError, match='Changed compressed payload'):
        verify_unchanged(original, output)


def test_bad_time_rejected(tmp_path):
    original, window = tmp_path / 'original', tmp_path / 'window'
    make(original)
    make(window, 2, True)
    mask = np.ones((2, 2), bool)
    with pytest.raises(ValueError, match='timestamp mismatch'):
        write_day(original, [window] * 24, list(reversed(range(24))), tmp_path / 'out',
                  tmp_path / 'scratch', date(2014, 1, 5), mask, mask, {})
    assert not (tmp_path / 'out').exists()
