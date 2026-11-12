"""Accepted full-history tile -> exact WY1980–WY2025 percentile table.

One SLURM array task per active tile; publication is isolated by campaign job.
"""
import argparse
import json
import os
import resource
import shutil
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from benchmark_nwm_climatology import digest, direct_oracle, publish, write_table
from netCDF4 import Dataset, num2date

from hydro_ops.wrf_hydro.climatology import (
    REFERENCE_END,
    REFERENCE_START,
    calendar_windows,
    dates,
    statistics,
)
from hydro_ops.wrf_hydro.production import write_json


def check_quantiles(q, count, windows):
    """Validate monotonicity and empirical counts without full-array temporaries."""
    for day in range(366):
        if np.any(np.diff(q[day], axis=0) < 0):
            raise ValueError('Nonmonotonic thresholds')
        if day == 59:
            if not np.all(count[day] == -1):
                raise ValueError('Invalid leap-day count sentinel')
        elif np.any((count[day] < 0) | (count[day] > len(windows[day]))):
            raise ValueError('Invalid sample counts')
    np.testing.assert_allclose(q[59], (q[58].astype('f8')+q[60].astype('f8'))/2,
                               rtol=2e-7, atol=1e-5, equal_nan=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history-job', required=True, type=int)
    parser.add_argument('--domain', choices=('cnrfc', 'ucrb'), default='cnrfc')
    args = parser.parse_args()
    domain = args.domain
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_ARRAY_JOB_ID']; index = int(os.environ['SLURM_ARRAY_TASK_ID'])
    task_job = os.environ['SLURM_JOB_ID']; user = os.environ['SLURM_JOB_USER']
    work = Path(f'/scratch/{user}/job_{task_job}/percentiles-{index:03d}')
    work.mkdir(parents=True, exist_ok=False)
    state = root / f'nwm/status/{domain}/climatology/tiles-{job}'
    state.mkdir(parents=True, exist_ok=True)
    # Exclusive claim prevents two processes writing the same task's publication.
    with (state / f'task-{index:03d}.claim').open('x') as claim:
        claim.write(task_job+'\n')
    report = {'status': 'running', 'domain': domain, 'array_job': job, 'task_index': index,
              'history_job': args.history_job, 'reference_start': str(REFERENCE_START),
              'reference_end': str(REFERENCE_END), 'product_status': 'validated tile candidate; domain audit pending'}
    started = time.monotonic()
    try:
        source_state = root / f'nwm/status/{domain}/climatology/history-stream-{args.history_job}'
        accepted = json.loads((source_state / 'status.json').read_text())
        if accepted.get('status') != 'passed':
            raise ValueError('History extraction has not passed acceptance')
        if accepted.get('domain', 'cnrfc') != domain:
            raise ValueError('History domain mismatch')
        report['source_model_campaign'] = accepted.get('source_model_campaign', 'legacy_unspecified')
        artifact = accepted['artifacts'][index]
        source = Path(artifact['path'])
        history = work / 'history.nc'
        shutil.copy2(source, history)
        if digest(history) != artifact['sha256']:
            raise ValueError('History copy checksum mismatch')
        report['tile'] = source.stem
        write_json(state / f'task-{index:03d}.json', report)
        results, counts = {}, {}
        with Dataset(history) as ds:
            input_dates = [date(t.year, t.month, t.day) for t in num2date(ds['time'][:], ds['time'].units)]
            if input_dates != dates(REFERENCE_START-timedelta(days=15), REFERENCE_END+timedelta(days=15)):
                raise ValueError('History does not match full padded reference period')
            windows = calendar_windows(input_dates)
            tile = {'window': json.loads(ds.tile_window), 'active': np.zeros((len(ds.dimensions['y']), len(ds.dimensions['x'])), bool),
                    'crs': {k: ds['crs'].getncattr(k) for k in ds['crs'].ncattrs() if k != '_FillValue'}}
            for name in ('lat', 'lon', 'x', 'y'):
                tile[name] = ds[name][:]
            report['fields'] = {}
            for name in ('SOIL_WATER', 'SNEQV'):
                tick = time.monotonic()
                values = np.ma.asarray(ds[name][:]).filled(np.nan)
                q, count = statistics(values, windows)
                check_quantiles(q, count, windows)
                valid = np.isfinite(values).any(axis=0)
                if not valid.any():
                    raise ValueError(f'No valid reference data: {name}')
                # Sparse coastal tiles may miss all nine rectangular oracle points.
                # Select nine known-valid cells instead, preserving their histories.
                points = np.flatnonzero(valid)
                points = points[np.linspace(0, len(points)-1, min(9, len(points)), dtype=int)]
                sampled = values.reshape(len(values), -1)[:, points, None]
                sampled_q = q.reshape(366, 100, -1)[:, :, points, None]
                comparisons = direct_oracle(sampled, windows, sampled_q)
                results[name], counts[name] = q, count
                report['fields'][name] = {'elapsed_seconds': time.monotonic()-tick,
                                         'oracle_comparisons': comparisons, 'valid_cells': int(valid.sum())}
                del values, sampled, sampled_q, q, count
                write_json(state / f'task-{index:03d}.json', report)
        target = work / 'percentiles.nc'
        write_table(target, tile, results, counts, windows)
        with Dataset(target, 'a') as ds:
            ds.product_status = 'validated tile candidate; full-domain acceptance pending'
            ds.source_history_sha256 = artifact['sha256']
            ds.source_history_job = str(args.history_job)
            ds.source_model_campaign = report['source_model_campaign']
            ds.domain = domain
        # Verify written thresholds one calendar date at a time.
        with Dataset(target) as ds:
            for name in results:
                for day in range(366):
                    np.testing.assert_array_equal(np.ma.asarray(ds[name][day]).filled(np.nan), results[name][day])
                    np.testing.assert_array_equal(ds[name+'_sample_count'][day], counts[name][day])
        output = root / f'nwm/analysis/{domain}/retro/climatology/wy1980-2025/tiles-{job}' / source.stem
        report['artifact'] = publish(target, output / 'percentiles.nc')
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report.update(elapsed_seconds=time.monotonic()-started,
                      max_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
        write_json(state / f'task-{index:03d}.json', report)


if __name__ == '__main__':
    main()
