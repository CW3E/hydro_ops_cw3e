#!/usr/bin/env python3
"""Isolated real-data CNRFC PRISM fallback/spatial-fit experiment, never publication."""

import argparse
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date
from scipy.spatial import cKDTree

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_safeguards import (
    allocate_daily_depth,
    audit_daily_constraint,
    fit_daily_depth,
    repair_local_fit,
    weak_support_targets,
)


def read_hours(path, variable):
    with Dataset(path) as ds:
        times = num2date(ds['time'][:], ds['time'].units)
        if len(times) != 24 or [t.hour for t in times] != list(range(24)):
            raise ValueError(f'Not a complete calendar day: {path}')
        values = np.ma.asarray(ds[variable][:], dtype='f8').filled(np.nan)
    return values


def compact_audit(result):
    return {k: v for k, v in result.items() if k not in ('failed_mask', 'residual_mm')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('day', type=date.fromisoformat)
    parser.add_argument('--max-iterations', type=int, default=2000)
    parser.add_argument('--local-fallback', action='store_true')
    parser.add_argument('--weak-support', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    output = root / 'forcing/work/prism-fallback-tests' / os.environ['SLURM_ARRAY_JOB_ID']
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    day = args.day
    previous = day - timedelta(days=1)
    mask_path = root / 'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc'
    with Dataset(mask_path) as ds:
        lat, lon = np.asarray(ds['lat'][:]), np.asarray(ds['lon'][:])
        window = json.loads(ds.source_window)
        active_source = np.asarray(ds['model_mask'][:], dtype=bool).reshape(-1)
    ny, nx = lat.shape
    origin_y, origin_x = window['south_north_start'], window['west_east_start']
    weights = root / 'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
    original = ConservativeOperator.from_cdo(weights)
    with Dataset(weights) as ds:
        global_nx = int(ds['src_grid_dims'][0])
        covered_fraction = np.asarray(ds['dst_grid_frac'][:])
    y, x = original.source_index // global_nx, original.source_index % global_nx
    inside = (y >= origin_y) & (y < origin_y + ny) & (x >= origin_x) & (x < origin_x + nx)
    source = (y[inside] - origin_y) * nx + x[inside] - origin_x
    target, weight = original.target_index[inside], original.weight[inside]
    full_weight = np.bincount(original.target_index, weights=original.weight,
                              minlength=original.target_size)
    local_weight = np.bincount(target, weights=weight, minlength=original.target_size)
    full = (full_weight > 0) & (local_weight >= full_weight * .999999)
    operator = ConservativeOperator(ny * nx, original.target_size, source, target, weight)
    del original, y, x, inside

    def published(d):
        return root / 'forcing/outputs/cnrfc/retro/hourly' / d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')

    old = np.concatenate((read_hours(published(previous), 'RAINRATE')[12:],
                          read_hours(published(day), 'RAINRATE')[:12])) * 3600
    flat = old.reshape(24, -1)
    # Select complete-support targets before fitting; preserve exactly this scope in the audit.
    complete = np.all(np.isfinite(flat) & (flat >= 0), axis=0)
    bad_support = np.bincount(target[~complete[source]], minlength=operator.target_size) > 0
    prism_path = root / 'forcing/inputs/oregon_state/prism/an/4km/daily/ppt' / day.strftime('%Y/%m') / f'prism_ppt_us_25m_{day:%Y%m%d}.nc'
    with Dataset(prism_path) as ds:
        prism = np.ma.asarray(ds['ppt'][0], dtype='f8').filled(np.nan).reshape(-1)
    required = full & ~bad_support & np.isfinite(prism)
    eligible = required.copy()
    weak = required & weak_support_targets(operator, covered_fraction, active_source)
    if args.weak_support:
        required &= ~weak
    else:
        weak[:] = False
    target_depth = np.where(required, prism, np.nan)
    report = {
        'day': str(day), 'status': 'running', 'production_modified': False,
        'scope': 'CNRFC complete-support PRISM cells; archived retro timing, not rebuilt baseline',
        'window': 'previous day 12 UTC through label day 11 UTC',
        'required_cells': int(required.sum()),
        'excluded_incomplete_support_cells': int((full & bad_support & np.isfinite(prism)).sum()),
        'before': compact_audit(audit_daily_constraint(flat, target_depth, operator)),
        'weak_support': {'enabled': args.weak_support, 'coverage_threshold': .05,
                         'eligible_cells': int(eligible.sum()), 'cells': int(weak.sum()),
                         'indices': np.flatnonzero(weak).tolist(),
                         'rule': 'fraction < 0.05 AND no active contributor; complete links required',
                         'estimation': 'shared cells follow hard fit; otherwise retain original hourly values'},
    }

    def nldas_path(d):
        return root / f'forcing/inputs/nasa/nldas2/fora0125_hourly_v2.0/{d:%Y}/NLDAS_FORA0125_H.A{d:%Y%m%d}.020.nc'

    with Dataset(nldas_path(day)) as ds:
        if ds['Rainf'].units != 'kg m-2':
            raise ValueError('Unexpected NLDAS hourly accumulation units')
        gx, gy = np.meshgrid(ds['lon'][:], ds['lat'][:])
    # Nearest regular-grid cell for timing ONLY; not a precipitation-total remap.
    tree = cKDTree(np.column_stack((gx.ravel(), gy.ravel())))
    _, donors = tree.query(np.column_stack((lon.ravel(), lat.ravel())))
    native = np.concatenate((read_hours(nldas_path(previous), 'Rainf')[12:],
                             read_hours(nldas_path(day), 'Rainf')[:12]))
    nldas = native.reshape(24, -1)[:, donors]
    baseline_prism = np.stack([operator.apply(a) for a in flat])
    nldas_prism = np.stack([operator.apply(a) for a in nldas])
    # Compact PRISM-grid control checks temporal fallback independent of spatial inversion.
    indices = np.flatnonzero(required)
    control, control_source = allocate_daily_depth(
        prism[indices], baseline_prism[:, indices], nldas_prism[:, indices]
    )
    report['prism_grid_control'] = {
        'max_sum_error_mm': float(np.max(np.abs(control.sum(axis=0) - prism[indices]))),
        'timing_source_counts': {str(k): int((control_source == k).sum()) for k in range(5)},
    }
    fit_started = time.monotonic()
    depth, fit_report = fit_daily_depth(target_depth, operator, max_iterations=args.max_iterations)
    report['spatial_fit'] = dict(fit_report, elapsed_seconds=time.monotonic() - fit_started)
    if args.local_fallback:
        local_started = time.monotonic()
        depth, local_report = repair_local_fit(depth, target_depth, operator)
        if local_report['status'] == 'local_solve_failed':
            first_report = local_report
            depth, local_report = repair_local_fit(depth, target_depth, operator,
                                                   neighborhood_rings=1, safety_margin=.99)
            local_report['initial_neighborhood'] = first_report
            if local_report['status'] == 'local_solve_failed':
                previous_report = local_report
                depth, local_report = repair_local_fit(depth, target_depth, operator,
                                                       neighborhood_rings=8, safety_margin=.99)
                local_report['smaller_neighborhood'] = previous_report
        report['local_fallback'] = dict(local_report, elapsed_seconds=time.monotonic()-local_started)
    hourly, provenance = allocate_daily_depth(depth, flat, nldas)
    if args.weak_support:
        # No hard spatial target: retain the original data, NOT synthetic zero or removal.
        unconstrained = ~np.isfinite(depth)
        hourly[:, unconstrained] = flat[:, unconstrained]
        provenance[unconstrained] = 0
        if not np.array_equal(np.isfinite(hourly), np.isfinite(flat)):
            raise AssertionError('Weak-support trial changed the original forcing coverage')
        report['weak_support']['forcing_coverage_preserved'] = True
    report['candidate'] = compact_audit(audit_daily_constraint(hourly, target_depth, operator))
    report['timing_source_counts'] = {str(k): int((provenance == k).sum()) for k in range(5)}
    # Write/read float32 rain rates as a diagnostic window, NOT a production daily collection.
    candidate = output / f'{day}.diagnostic-prism-window.nc'
    with Dataset(candidate, 'w') as ds:
        ds.createDimension('time', 24)
        ds.createDimension('y', ny)
        ds.createDimension('x', nx)
        ds.description = 'Experimental PRISM window; NOT calendar-day model forcing'
        ds.prism_support_policy = 'inactive_below_5pct_diagnostic_only_v1' if args.weak_support else 'all_eligible_hard'
        ds.prism_weak_support_note = 'Weak targets are reported, not certified as PRISM-matched; source masks unchanged'
        t = ds.createVariable('time', 'i8', ('time',))
        t.units = f'hours since {previous} 12:00:00'
        t[:] = np.arange(24)
        v = ds.createVariable('RAINRATE', 'f4', ('time', 'y', 'x'), zlib=True, complevel=2,
                              chunksizes=(1, min(ny, 128), min(nx, 128)), fill_value=-9999.)
        v.units = 'kg m-2 s-1'
        v[:] = np.ma.masked_invalid(hourly.reshape(24, ny, nx) / 3600)
        p = ds.createVariable('timing_source', 'u1', ('y', 'x'), zlib=True)
        p.flag_values = np.arange(5, dtype='u1')
        p.flag_meanings = 'unconstrained baseline nldas uniform dry'
        p[:] = provenance.reshape(ny, nx)
    with Dataset(candidate) as ds:
        roundtrip = np.ma.asarray(ds['RAINRATE'][:], dtype='f8').filled(np.nan) * 3600
    audit = audit_daily_constraint(roundtrip, target_depth, operator)
    report['roundtrip'] = compact_audit(audit)
    all_residual = operator.apply(np.sum(roundtrip.reshape(24, -1), axis=0)) - prism
    report['weak_support']['residual_mm'] = all_residual[weak].tolist()
    report['weak_support']['targets_mm'] = prism[weak].tolist()
    report['weak_support']['covered_fraction'] = covered_fraction[weak].tolist()
    report['weak_support']['max_abs_residual_mm'] = float(np.max(np.abs(all_residual[weak]))) if weak.any() else 0.
    np.savez_compressed(output / f'{day}.residuals.npz', target_mm=target_depth,
                        weak_target_mm=np.where(weak, prism, np.nan),
                        before_mm=operator.apply(flat.sum(axis=0)),
                        residual_mm=audit['residual_mm'], failed_mask=audit['failed_mask'])
    report['status'] = 'passed' if audit['accepted'] else 'rejected'
    report['elapsed_seconds'] = time.monotonic() - started
    (output / f'{day}.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    # Execution completion and scientific acceptance are intentionally separate.


if __name__ == '__main__':
    main()
