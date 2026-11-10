#!/usr/bin/env python3
"""Exercise the operational calendar assembler with isolated PRISM candidates."""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, date2num, num2date

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_safeguards import audit_daily_constraint


def stage_window(source, destination):
    """Make timing provenance time-dependent so each half retains its own policy."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(source) as original, Dataset(destination, 'w') as output:
        output.setncatts({name: original.getncattr(name) for name in original.ncattrs()})
        for name, dimension in original.dimensions.items():
            output.createDimension(name, len(dimension))
        output.description = 'ISOLATED rainfall-only test; NOT model-ready forcing'
        for name, variable in original.variables.items():
            dims = ('time', 'y', 'x') if name == 'timing_source' else variable.dimensions
            kwargs = {'fill_value': variable._FillValue} if '_FillValue' in variable.ncattrs() else {}
            created = output.createVariable(name, variable.dtype, dims, zlib=True,
                                             complevel=2, **kwargs)
            created.setncatts({a: variable.getncattr(a) for a in variable.ncattrs()
                              if a != '_FillValue'})
            if name == 'time':
                created.units = 'hours since 1970-01-01 00:00:00'
                created[:] = date2num(num2date(variable[:], variable.units), created.units)
            else:
                created[:] = variable[:]


def stamps(path):
    with Dataset(path) as ds:
        t = ds['time']
        return [v.strftime('%Y-%m-%dT%H:%M:%S') for v in num2date(t[:], t.units)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('start', type=date.fromisoformat)
    parser.add_argument('--local-fallback', action='store_true')
    parser.add_argument('--weak-support', action='store_true')
    args = parser.parse_args()
    started = time.monotonic()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    campaign = root / 'forcing/work/prism-fallback-tests' / os.environ['SLURM_ARRAY_JOB_ID']
    output = campaign / f'calendar-{args.start}'
    output.mkdir(parents=True, exist_ok=False)
    windows = [args.start + timedelta(days=i) for i in range(3)]
    for day in windows:
        subprocess.run([sys.executable, str(root / 'bin/test_prism_fallback_strategy.py'),
                        str(day), '--max-iterations', '2000' if args.local_fallback else '10000',
                        *(['--local-fallback'] if args.local_fallback else []),
                        *(['--weak-support'] if args.weak_support else [])], check=True)
        result = json.loads((campaign / f'{day}.json').read_text())
        if result['status'] != 'passed':
            raise RuntimeError(f'Window rejected: {day}')
        destination = output / 'windows' / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        stage_window(campaign / f'{day}.diagnostic-prism-window.nc', destination)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    scratch.mkdir(parents=True, exist_ok=True)
    assembly_started = time.monotonic()
    env = {**os.environ, 'HYDRO_OPS_ARCHIVE_CHUNKS': '1'}
    subprocess.run([sys.executable, str(root / 'bin/materialize_calendar_forcing.py'),
                    '--input-root', str(output / 'windows'), '--output-root', str(output / 'calendar'),
                    '--start', str(args.start), '--days', '2', '--hierarchical',
                    '--work-directory', str(scratch)], check=True, env=env)
    assembly_seconds = time.monotonic() - assembly_started
    assembled = []
    file_reports = []
    for day in windows[:2]:
        path = output / 'calendar' / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        actual = stamps(path)
        expected = [f'{day}T{h:02d}:00:00' for h in range(24)]
        if actual != expected:
            raise AssertionError(f'Wrong calendar timestamps: {path}')
        with Dataset(path) as ds:
            # Exact equality includes masks and per-hour provenance, not just totals.
            for half, source_day, source_slice in ((0, day, slice(12, 24)),
                                                   (1, day + timedelta(days=1), slice(0, 12))):
                window = output / 'windows' / source_day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                with Dataset(window) as source:
                    for name in ('RAINRATE', 'timing_source'):
                        a, b = ds[name][half * 12:(half + 1) * 12], source[name][source_slice]
                        np.testing.assert_array_equal(np.ma.getmaskarray(a), np.ma.getmaskarray(b))
                        np.testing.assert_array_equal(a.compressed(), b.compressed())
            assembled.append(np.ma.asarray(ds['RAINRATE'][:], dtype='f8').filled(np.nan) * 3600)
        manifest = json.loads(path.with_suffix(path.suffix + '.manifest.json').read_text())
        file_reports.append({'path': str(path), 'hours': 24, 'exact_values_and_masks': True,
                             'writer': manifest.get('archive_writer', 'standard')})
    # Reconstitute the complete middle PRISM window solely from the two calendar files.
    hourly = np.concatenate((assembled[0][12:], assembled[1][:12]))
    with Dataset(root / 'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc') as ds:
        region = json.loads(ds.source_window)
    ny, nx = hourly.shape[1:]
    weights = root / 'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
    original = ConservativeOperator.from_cdo(weights)
    with Dataset(weights) as ds:
        width = int(ds['src_grid_dims'][0])
    y, x = original.source_index // width, original.source_index % width
    y0, x0 = region['south_north_start'], region['west_east_start']
    inside = (y >= y0) & (y < y0 + ny) & (x >= x0) & (x < x0 + nx)
    operator = ConservativeOperator(ny * nx, original.target_size,
                                    (y[inside] - y0) * nx + x[inside] - x0,
                                    original.target_index[inside], original.weight[inside])
    with np.load(campaign / f'{windows[1]}.residuals.npz') as ds:
        target = ds['target_mm']
        weak_target = ds['weak_target_mm']
    audit = audit_daily_constraint(hourly, target, operator)
    weak_mask = np.isfinite(weak_target)
    weak_residual = operator.apply(hourly.sum(axis=0)) - weak_target
    # Exercise actual operational discovery on duplicate and missing windows too.
    spec = importlib.util.spec_from_file_location('calendar_cli', root / 'bin/materialize_calendar_forcing.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    staged = output / 'windows' / windows[0].strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    duplicate = staged.with_suffix(staged.suffix + '.nc')
    duplicate.symlink_to(staged.resolve())
    try:
        try:
            module.locate_hours(output / 'windows', windows[0])
        except ValueError:
            duplicate_rejected = True
        else:
            raise AssertionError('Duplicate hours accepted')
    finally:
        duplicate.unlink()  # Only the temporary test link created immediately above.
    try:
        module.locate_hours(output / 'windows', windows[0] - timedelta(days=1))
    except FileNotFoundError:
        missing_rejected = True
    else:
        raise AssertionError('Missing boundary hours accepted')
    report = {'status': 'passed' if audit['accepted'] else 'rejected',
              'production_modified': False, 'scope': 'rainfall-only, complete-support targets',
              'files': file_reports, 'prism_window': str(windows[1]),
              'required_cells': audit['required_cells'], 'failed_cells': audit['failed_cells'],
              'weak_support_cells': int(weak_mask.sum()),
              'weak_support_residual_mm': weak_residual[weak_mask].tolist(),
              'weak_support_indices': np.flatnonzero(weak_mask).tolist(),
              'duplicate_hours_rejected': duplicate_rejected, 'missing_hours_rejected': missing_rejected,
              'assembly_seconds': assembly_seconds, 'elapsed_seconds': time.monotonic() - started}
    (output / 'acceptance.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    if not audit['accepted']:
        raise RuntimeError('Calendar reconstruction failed PRISM audit')


if __name__ == '__main__':
    main()
