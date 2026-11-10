"""Reuse wave-7 accepted windows; replace failed acceptance only after full audit."""
import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from materialize_calendar_forcing import locate_hours, prism_window_metadata
from netCDF4 import Dataset

from hydro_ops.forcing.rain_envelope_writer import write_enveloped_day
from hydro_ops.forcing.rain_repair_writer import digest
from hydro_ops.forcing.repair_transaction import atomic_json, check_snapshot


def task(options):
    root, candidate, output, scratch, day = options
    with Dataset(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc') as ds:
        active, keep = np.asarray(ds['active'][:], bool), np.asarray(ds['keep'][:], bool)
    paths, indices = locate_hours(candidate/'windows', day)
    metadata = prism_window_metadata(paths, 'retro')
    metadata.update(archive_period=str(day), archive_granularity='utc_calendar_day',
                    calendar_day_start_utc=f'{day}T00:00:00Z', calendar_day_end_utc=f'{day}T23:00:00Z')
    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    original = root/'forcing/work/prism-repair-wave7-202101-20260417/originals'/relative
    return write_enveloped_day(original, paths, indices, output/'hourly'/relative,
                               scratch/day.isoformat(), day, active, keep, metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--canary', action='store_true')
    parser.add_argument('--gate', type=Path)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    wave = root/'forcing/work/prism-repair-wave7-202101-20260417'
    check_snapshot(wave/'originals/snapshot.json', wave/'originals')
    job = os.environ['SLURM_JOB_ID']
    output = root/'forcing/work/retro-envelope-reassembly'/job
    output.mkdir(parents=True, exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/envelope-reassembly')
    if args.canary:
        periods = [(62, date(2026, 3, 3), 1), (63, date(2026, 4, 1), 1)]
    else:
        if args.gate is None or json.loads(args.gate.read_text()).get('status') != 'passed':
            raise ValueError('Envelope canary not accepted')
        index = int(os.environ['SLURM_ARRAY_TASK_ID'])
        periods = {62: [(62, date(2026, 3, 1), 31)], 63: [(63, date(2026, 4, 1), 17)]}[index]
    report = {'status': 'running', 'production_modified': False, 'files': [],
              'reused_accepted_windows': True, 'canary': args.canary}
    began = time.monotonic()
    try:
        for index, first, days in periods:
            candidate = root/f'forcing/work/prism-rain-pipeline/5174006_{index}'
            old_path = candidate/'acceptance.json'
            old = json.loads(old_path.read_text())
            if old['status'] != 'failed' or old.get('error') != 'Invalid rainfall or coverage':
                raise ValueError('Unexpected original failed-candidate status')
            if len(old['windows']) != (32 if index == 62 else 18):
                raise ValueError('Incomplete accepted window set')
            for window in old['windows']:
                proof = json.loads(Path(window['report']).read_text())
                if proof['status'] != 'published_candidate':
                    raise ValueError('Window scientific audit not accepted')
            options = [(root, candidate, output, scratch, first+timedelta(days=i)) for i in range(days)]
            with ProcessPoolExecutor(max_workers=2) as pool:
                for row in pool.map(task, options):
                    report['files'].append(row)
                    atomic_json(output/'acceptance.json', report)
                    print(row['path'], flush=True)
            if args.canary:
                with Dataset(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc') as ds:
                    active, keep = np.asarray(ds['active'][:], bool), np.asarray(ds['keep'][:], bool)
                for row in report['files'][-days:]:
                    with Dataset(row['path']) as ds:
                        for name in ('T2D', 'Q2D', 'PSFC', 'U2D', 'V2D', 'SWDOWN', 'LWDOWN'):
                            for hour in range(24):
                                values = np.ma.asarray(ds[name][hour], dtype='f8').filled(np.nan)
                                if not np.isfinite(values[active]).all() or np.isfinite(values[~keep]).any():
                                    raise ValueError(f'Original nonrain envelope/coverage violation: {name}, {row["path"]}, {hour}')
                    row['nonrain_active_and_envelope_verified'] = True
            if not args.canary:
                if len(report['files']) != days:
                    raise ValueError('Incomplete calendar assembly')
                backup = candidate/'acceptance.before-envelope-reassembly.json'
                with backup.open('x') as handle:
                    json.dump(old, handle, indent=2)
                revised = dict(old)
                revised.pop('error', None)
                revised.update(status='passed', scientific_validation=True, files=report['files'],
                               writer_stage_seconds=time.monotonic()-began,
                               elapsed_seconds=old['window_stage_seconds']+time.monotonic()-began,
                               envelope_reassembly=str(output/'acceptance.json'),
                               previous_failed_acceptance=str(backup),
                               previous_failed_acceptance_sha256=digest(backup))
                report['replacement_acceptance'] = str(old_path)
                # All candidates are audited before the failed month can become accepted.
                atomic_json(old_path, revised)
        report.update(status='passed', elapsed_seconds=time.monotonic()-began)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(output/'acceptance.json', report)


if __name__ == '__main__':
    main()
