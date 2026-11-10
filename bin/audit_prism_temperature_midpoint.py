"""Read-only numerical PRISM-window midpoint diagnostic; never publish or ack.

The affine temperature correction preserves (min+max)/2 even if its scale is
clipped. A mismatch diagnoses inconsistency with today's target, not its cause.
A match alone cannot establish range/hourly-curve or coupled-field equivalence.
"""
import argparse
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.prism_temperature import create_prism_temperature_constraints
from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json

CASES = ['2026-03-02', '2026-03-05', '2026-04-12', '2025-12-15',
         '2026-09-15', '2026-10-25', '2026-04-05']


def statistics(actual, expected, mask):
    valid = mask & np.isfinite(actual) & np.isfinite(expected)
    delta = actual[valid] - expected[valid]
    absolute = np.abs(delta)
    return {'cells': int(valid.sum()), 'bias_k': float(delta.mean()) if delta.size else None,
            'max_abs_k': float(absolute.max()) if delta.size else None,
            'p99_abs_k': float(np.percentile(absolute, 99)) if delta.size else None,
            'cells_above_0_001k': int((absolute > .001).sum()),
            'cells_above_0_1k': int((absolute > .1).sum())}


def extrema(root, stream, day, shape):
    low = np.full(shape, np.inf)
    high = np.full(shape, -np.inf)
    count = np.zeros(shape, dtype=np.uint8)
    donor_free = np.ones(shape, dtype=bool)
    start = datetime.combine(day-timedelta(days=1), time(12))
    wanted = {start+timedelta(hours=i) for i in range(24)}
    seen = set()
    pins = {}
    for d in (day-timedelta(days=1), day):
        path = root/f'forcing/outputs/conus/{stream}/hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        pins[str(path)] = list(fingerprint(path))
        with Dataset(path) as ds:
            t = ds['time']
            times = num2date(t[:], t.units, calendar=getattr(t, 'calendar', 'standard'),
                            only_use_cftime_datetimes=False, only_use_python_datetimes=True)
            for i, stamp in enumerate(times):
                if stamp not in wanted:
                    continue
                if stamp in seen:
                    raise ValueError('Duplicate window record')
                seen.add(stamp)
                values = np.ma.asarray(ds['T2D'][i]).filled(np.nan).squeeze()
                if values.shape != shape:
                    raise ValueError('Forcing/target grid mismatch')
                valid = np.isfinite(values)
                low = np.minimum(low, np.where(valid, values, np.inf))
                high = np.maximum(high, np.where(valid, values, -np.inf))
                count += valid
                if 'native_donor_distance_km' in ds.variables:
                    var = ds['native_donor_distance_km']
                    distance = var[i] if var.dimensions[0] == 'time' else var[:]
                    donor_free &= np.ma.asarray(distance).filled(np.nan).squeeze() == 0
                else:
                    donor_free[:] = False
    if seen != wanted:
        raise ValueError('Incomplete 12 UTC constraint window')
    midpoint = np.full(shape, np.nan)
    complete = count == 24
    midpoint[complete] = (low[complete]+high[complete])/2
    return midpoint, donor_free, pins


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=int, required=True)
    parser.add_argument('--plan', type=Path, help='Frozen read-only diagnostic day list')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    job = os.environ['SLURM_JOB_ID']
    cases = json.loads(args.plan.read_text())['days'] if args.plan else CASES
    if not 0 <= args.case < len(cases):
        parser.error('Case outside frozen plan')
    day = date.fromisoformat(cases[args.case])
    report_path = root/f'forcing/work/temperature-reconciliation/{job}/report.json'
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/temperature-reconciliation')
    scratch.mkdir(parents=True, exist_ok=False)
    layout = OperationalLayout.project_defaults(root)
    sources = {v: root/'forcing/inputs/oregon_state/prism/an/4km/daily'/v/day.strftime(
        f'%Y/%m/prism_{v}_us_25m_%Y%m%d.nc') for v in ('tmin', 'tmax')}
    pins = {v: source_signature(p) for v, p in sources.items()}
    report = {'status': 'running', 'day': str(day), 'job': job,
              'production_modified': False, 'queue_modified': False,
              'prism_sources': pins, 'streams': {},
              'interpretation': 'Diagnostic only; midpoint match is not full-field acceptance'}
    try:
        constraint = scratch/'constraint.nc'
        create_prism_temperature_constraints(
            sources['tmin'], sources['tmax'], root/'forcing/static/prism/prism_an_4km_elevation.nc',
            layout.target_grid, layout.target_elevation,
            root/'forcing/static/remapping/nwm_conus_1km/prism_bilinear.nc',
            constraint, work_directory=scratch)
        with Dataset(constraint) as ds:
            lo = np.ma.asarray(ds['prism_tmin'][:]).filled(np.nan)
            hi = np.ma.asarray(ds['prism_tmax'][:]).filled(np.nan)
            expected = (lo.astype(float)+hi.astype(float))/2
        with Dataset(layout.target_grid) as ds:
            active = np.asarray(ds['active_domain'][:], dtype=bool)
        mask = active & np.isfinite(lo) & np.isfinite(hi) & (hi >= lo)
        for stream in ('retro', 'nrt'):
            paths = [root/f'forcing/outputs/conus/{stream}/hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                     for d in (day-timedelta(days=1), day)]
            if not all(p.exists() for p in paths):
                report['streams'][stream] = {'status': 'skipped_missing_window'}
                continue
            actual, donor_free, inputs = extrema(root, stream, day, expected.shape)
            if any(list(fingerprint(Path(p))) != pin for p, pin in inputs.items()):
                raise ValueError('Production forcing changed during diagnostic')
            report['streams'][stream] = {'status': 'diagnosed', 'forcing_pins': inputs,
                'active': statistics(actual, expected, mask),
                'active_without_native_donors': statistics(actual, expected, mask & donor_free)}
            atomic_json(report_path, report)
        if any(source_signature(sources[v]) != pin for v, pin in pins.items()):
            raise ValueError('PRISM changed during diagnostic')
        report['status'] = 'diagnosed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(report_path, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
