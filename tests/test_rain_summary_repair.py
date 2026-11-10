"""Rain-only summaries match full regeneration while preserving non-rain data."""
# NetCDF helper dates intentionally use naive UTC.
# ruff: noqa: DTZ001
import json
import shutil
from datetime import date, datetime, timedelta

import numpy as np
import pytest
from netCDF4 import Dataset, date2num

from hydro_ops.forcing.rain_summary_repair import repair_summary, verify_nonrain
from hydro_ops.forcing.temporal_summary import TIME_UNITS, input_path, summarize, summary_path

SPEC = {'reducers': {'T2D': 'mean', 'RAINRATE': 'mean'}, 'output_names': {}, 'output_units': {}}


def hourly(root, day):
    path = root/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, 'w') as ds:
        for k, n in [('time', 24), ('y', 2), ('x', 2)]:
            ds.createDimension(k, n)
        t = ds.createVariable('time', 'f8', ('time',))
        t.units = TIME_UNITS
        t[:] = date2num([datetime(day.year, day.month, day.day)+timedelta(hours=h)
                        for h in range(24)], TIME_UNITS)
        for name in SPEC['reducers']:
            v = ds.createVariable(name, 'f4', ('time', 'y', 'x'), fill_value=-9999)
            v.units = 'K' if name == 'T2D' else 'kg m-2 s-1'
            v[:] = 280 if name == 'T2D' else day.day / 10000
            v[:, 1, 1] = -9999
    return path


def reduce(root, out, start, stop, **kwargs):
    summarize(root, out, start, stop, SPEC['reducers'], {}, {}, domain='test', stream='retro', **kwargs)


def test_daily_rain_repair_exact_and_invalid_provenance(tmp_path):
    day = date(1986, 10, 1)
    old = tmp_path/'old.nc'
    prior = [hourly(tmp_path/'original', d) for d in (day, day+timedelta(days=1))]
    reduce(tmp_path/'original', old, day, day+timedelta(days=1))
    shutil.copytree(tmp_path/'original', tmp_path/'current')
    current = [input_path(tmp_path/'current', d) for d in (day, day+timedelta(days=1))]
    for p in current:
        with Dataset(p, 'r+') as ds:
            ds['RAINRATE'][:] *= 3
    candidate = tmp_path/'candidate.nc'
    repair_summary(old, candidate, current, prior, day, day+timedelta(days=1), SPEC)
    ref = tmp_path/'reference.nc'
    reduce(tmp_path/'current', ref, day, day+timedelta(days=1))
    verify_nonrain(old, candidate)
    with Dataset(candidate) as a, Dataset(ref) as b:
        for name in SPEC['reducers']:
            np.testing.assert_array_equal(a[name][:], b[name][:])
        assert a.aggregation_signature == b.aggregation_signature
    with Dataset(old, 'r+') as ds:
        entries = json.loads(ds.source_files)
        entries[0]['bytes'] += 1
        ds.source_files = json.dumps(entries)
    with pytest.raises(ValueError, match='signature invalid'):
        repair_summary(old, tmp_path/'bad.nc', current, prior, day, day+timedelta(days=1), SPEC)


def test_monthly_reuses_nonrain_and_matches_full(tmp_path):
    start, stop = date(1984, 2, 1), date(1984, 3, 1)
    days = [start+timedelta(days=i) for i in range(29)]
    for day in [*days, stop]:
        hourly(tmp_path/'hourly', day)
    for day in days:
        reduce(tmp_path/'hourly', summary_path(tmp_path/'old-daily', day), day, day+timedelta(days=1))
    old = tmp_path/'old-month.nc'
    reduce(tmp_path/'old-daily', old, start, stop, from_daily=True)
    shutil.copytree(tmp_path/'old-daily', tmp_path/'current-daily')
    for day in days:
        with Dataset(summary_path(tmp_path/'current-daily', day), 'r+') as ds:
            ds['RAINRATE'][:] *= 2
    candidate = tmp_path/'new-month.nc'
    repair_summary(old, candidate, [summary_path(tmp_path/'current-daily', d) for d in days],
                   [summary_path(tmp_path/'old-daily', d) for d in days],
                   start, stop, SPEC, monthly=True)
    ref = tmp_path/'ref-month.nc'
    reduce(tmp_path/'current-daily', ref, start, stop, from_daily=True)
    verify_nonrain(old, candidate)
    with Dataset(candidate) as a, Dataset(ref) as b:
        np.testing.assert_array_equal(a['RAINRATE'][:], b['RAINRATE'][:])
        assert a.aggregation_signature == b.aggregation_signature


def test_nonrain_corruption_rejected(tmp_path):
    path = hourly(tmp_path/'hourly', date(1981, 1, 1))
    changed = tmp_path/'changed.nc'
    shutil.copy2(path, changed)
    with Dataset(changed, 'r+') as ds:
        ds['T2D'][0, 0, 0] = 300
    with pytest.raises(AssertionError):
        verify_nonrain(path, changed)
