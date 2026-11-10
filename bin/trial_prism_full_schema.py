#!/usr/bin/env python3
"""Isolated full-schema integration of accepted experimental precipitation."""

import json
import os
import shutil
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_safeguards import audit_daily_constraint

CASES = ['2002-01-15', '2016-04-15', '2023-01-15', '2000-02-28']
FIELDS = ['RAINRATE', 'T2D', 'Q2D', 'PSFC', 'LWDOWN', 'SWDOWN', 'U2D', 'V2D']


def equal(a, b):
    np.testing.assert_array_equal(np.ma.getmaskarray(a), np.ma.getmaskarray(b))
    np.testing.assert_array_equal(np.ma.asarray(a).compressed(), np.ma.asarray(b).compressed())


def identity(path):
    s = path.stat()
    return [s.st_size, s.st_mtime_ns, s.st_ino]


def integrate(original, rain, destination):
    """Copy a full archive, replace rainfall and invalidate superseded provenance."""
    before = identity(original)
    shutil.copy2(original, destination)
    with Dataset(rain) as candidate, Dataset(destination, 'r+') as ds:
        a, b = ds['time'], candidate['time']
        if list(num2date(a[:], a.units)) != list(num2date(b[:], b.units)):
            raise ValueError('Candidate timestamps differ from original')
        renamed = {}
        for name in list(ds.variables):
            if name.startswith('precip_') or name == 'forcing_qc_flags':
                renamed[name] = f'historical_{name}'
                ds.renameVariable(name, renamed[name])
                ds[renamed[name]].provenance_status = 'Historical only; not validation of trial rainfall'
        for name in list(ds.ncattrs()):
            if name.startswith(('prism_', 'precipitation_', 'parent_', 'subset_source',
                                'forcing_recovery', 'calendar_day_source_root')):
                ds.setncattr(f'historical_{name}', ds.getncattr(name))
                ds.delncattr(name)
        ds.title = 'ISOLATED full-schema PRISM precipitation trial; not operationally published'
        ds.trial_source = str(original)
        ds.trial_precipitation_source = str(rain)
        ds.trial_precipitation_policy = 'inactive_below_5pct_diagnostic_only_v1'
        ds.trial_precipitation_acceptance = 'pending independent read-back audit'
        ds.trial_note = 'Seven meteorological fields unchanged; historical QC does not describe new rain'
        ds['RAINRATE'][:] = candidate['RAINRATE'][:]
        v = ds.createVariable('trial_precip_timing_source_id', 'u1', ('time', 'y', 'x'),
                              zlib=True, complevel=2,
                              chunksizes=(1, min(128, len(ds.dimensions['y'])),
                                          min(128, len(ds.dimensions['x']))))
        source = candidate['timing_source']
        v.setncatts({n: source.getncattr(n) for n in source.ncattrs() if n != '_FillValue'})
        v[:] = source[:]
    with Dataset(original) as old, Dataset(rain) as candidate, Dataset(destination) as ds:
        for name in old.variables:
            result_name = renamed.get(name, name)
            expected = candidate[name] if name == 'RAINRATE' else old[name]
            actual = ds[result_name]
            assert actual.shape == expected.shape
            if actual.ndim == 3 and actual.dimensions[0] == 'time':
                for h in range(24):
                    equal(actual[h], expected[h])
            else:
                equal(actual[:], expected[:])
        equal(ds['trial_precip_timing_source_id'][:], candidate['timing_source'][:])
        active = np.asarray(ds['subset_model_mask'][:], dtype=bool)
        for name in FIELDS:
            for h in range(24):
                value = np.ma.asarray(ds[name][h], dtype='f8').filled(np.nan)
                if not np.all(np.isfinite(value[active])):
                    raise ValueError(f'Missing active-cell values: {name} hour {h}')
                if name in ('RAINRATE', 'Q2D', 'SWDOWN', 'LWDOWN') and np.any(value[active] < 0):
                    raise ValueError(f'Negative {name}')
                if name in ('T2D', 'PSFC') and np.any(value[active] <= 0):
                    raise ValueError(f'Nonpositive {name}')
            assert ds[name].filters()['zlib']
            if name == 'RAINRATE':
                for h in range(24):
                    equal(np.ma.getmaskarray(ds[name][h]), np.ma.getmaskarray(old[name][h]))
        times = num2date(ds['time'][:], ds['time'].units)
        assert len(times) == 24 and [t.hour for t in times] == list(range(24))
    assert identity(original) == before
    return {'original': str(original), 'original_identity': before, 'nonrain_values_unchanged': True,
            'masks_unchanged': True, 'active_cell_coverage': True, 'compressed_fields': FIELDS,
            'historical_diagnostics': renamed}


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    start = date.fromisoformat(CASES[int(os.environ['SLURM_ARRAY_TASK_ID'])])
    campaign = root / 'forcing/work/prism-fallback-tests/5081269'
    accepted = campaign / f'calendar-{start}'
    previous = json.loads((accepted / 'acceptance.json').read_text())
    if previous['status'] != 'passed':
        raise ValueError('Unaccepted precipitation candidate')
    output = root / 'forcing/work/prism-full-schema-trial' / os.environ['SLURM_ARRAY_JOB_ID'] / str(start)
    output.mkdir(parents=True, exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    scratch.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    reports, staged = [], []
    with tempfile.TemporaryDirectory(dir=scratch, prefix='prism-full-schema-') as temporary:
        for offset in range(2):
            day = start + timedelta(days=offset)
            suffix = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            original = root / 'forcing/outputs/cnrfc/retro/hourly' / suffix
            rain = accepted / 'calendar' / suffix
            target = Path(temporary) / original.name
            reports.append(integrate(original, rain, target))
            staged.append((target, output / 'hourly' / suffix))
        hourly = []
        for (path, _), selection in zip(staged, (slice(12, 24), slice(0, 12)), strict=True):
            with Dataset(path) as ds:
                hourly.append(np.ma.asarray(ds['RAINRATE'][selection], dtype='f8').filled(np.nan)*3600)
        hourly = np.concatenate(hourly)
        with Dataset(root / 'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc') as ds:
            region = json.loads(ds.source_window)
        weights = root / 'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
        op = ConservativeOperator.from_cdo(weights)
        with Dataset(weights) as ds:
            width = int(ds['src_grid_dims'][0])
        y, x = op.source_index // width, op.source_index % width
        y0, x0 = region['south_north_start'], region['west_east_start']
        ny, nx = hourly.shape[1:]
        keep = (y >= y0) & (y < y0+ny) & (x >= x0) & (x < x0+nx)
        operator = ConservativeOperator(ny*nx, op.target_size, (y[keep]-y0)*nx+x[keep]-x0,
                                        op.target_index[keep], op.weight[keep])
        with np.load(campaign / f'{start + timedelta(days=1)}.residuals.npz') as ds:
            target, weak = ds['target_mm'], ds['weak_target_mm']
        audit = audit_daily_constraint(hourly, target, operator)
        if not audit['accepted']:
            raise ValueError('Full-schema readback failed hard PRISM constraints')
        residual = operator.apply(hourly.sum(axis=0))-weak
        for src, dst in staged:
            with Dataset(src, 'r+') as ds:
                ds.trial_precipitation_acceptance = 'passed declared hard-support scope; weak targets diagnostic only'
            dst.parent.mkdir(parents=True, exist_ok=True)
            part = dst.with_name(dst.name+'.part')
            shutil.copy2(src, part)
            # Verify permanent bytes, not merely the scratch copy.
            import hashlib
            def digest(path):
                with path.open('rb') as stream:
                    return hashlib.file_digest(stream, 'sha256').hexdigest()
            checksum = digest(src)
            if digest(part) != checksum:
                raise ValueError('Permanent copy checksum mismatch')
            part.replace(dst)
            reports[staged.index((src, dst))].update(path=str(dst), sha256=checksum)
    report = {'status': 'passed', 'production_modified': False, 'kind': 'full-schema integration, not raw-source rebuild or model run',
              'files': reports, 'hard_cells': audit['required_cells'], 'failed_hard_cells': audit['failed_cells'],
              'weak_indices': np.flatnonzero(np.isfinite(weak)).tolist(),
              'weak_residual_mm': residual[np.isfinite(weak)].tolist(),
              'elapsed_seconds': time.monotonic()-started}
    (output / 'acceptance.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
