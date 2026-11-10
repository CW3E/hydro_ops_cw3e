#!/usr/bin/env python3
"""Compare isolated copy/replace candidates to a fully accepted month trial."""
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

from hydro_ops.forcing.prism_support_production import candidate_path
from hydro_ops.forcing.rain_repair_writer import write_day


def task(options):
    day, original, reference, windows, destination, work, mask, compare = options
    paths, indices = locate_hours(windows, day)
    metadata = prism_window_metadata(paths, 'retro')
    metadata.update(archive_period=str(day), archive_granularity='utc_calendar_day',
                    calendar_day_start_utc=f'{day}T00:00:00Z',
                    calendar_day_end_utc=f'{day}T23:00:00Z')
    # Preserve per-window acceptance classes instead of claiming strict convergence.
    classes = {}
    for path in set(paths):
        with Dataset(path) as ds:
            classes[ds.prism_day] = ds.prism_acceptance_class
    metadata['prism_acceptance_classes'] = json.dumps(classes, sort_keys=True)
    with Dataset(mask) as ds:
        active, keep = np.asarray(ds['active'][:], bool), np.asarray(ds['keep'][:], bool)
    return write_day(original, paths, indices, destination, work, day, active, keep,
                     metadata, reference=reference if compare else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accepted-trial', required=True, type=Path)
    parser.add_argument('--original-root', required=True, type=Path)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--days', type=int, default=3)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--compare-reference', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.days <= 31 or not 1 <= args.workers <= 4:
        parser.error('Require 1–31 days and 1–4 workers')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    accepted = json.loads((args.accepted_trial / 'acceptance.json').read_text())
    if accepted['status'] != 'passed' or not accepted['rain_only']:
        raise ValueError('Require accepted rain-only reference')
    accepted_paths = {Path(f['path']).resolve() for f in accepted['files']}
    job = os.environ['SLURM_JOB_ID']
    output = candidate_path(root / 'forcing/work/prism-rain-writer' / job, root)
    output.mkdir(parents=True, exist_ok=False)
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}/rain-writer")
    mask = root / 'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
    report = {'status': 'running', 'production_modified': False, 'files': [],
              'workers': args.workers, 'compare_reference': args.compare_reference,
              'accepted_trial': str(args.accepted_trial)}
    started = time.monotonic()

    def save():
        part = output / 'acceptance.json.part'
        part.write_text(json.dumps(report, indent=2) + '\n')
        part.replace(output / 'acceptance.json')

    save()
    try:
        tasks = []
        for i in range(args.days):
            day = args.start + timedelta(days=i)
            relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            ref = args.accepted_trial / 'hourly' / relative
            if ref.resolve() not in accepted_paths:
                raise ValueError(f'Unaccepted reference: {ref}')
            tasks.append((day, args.original_root / relative, ref,
                          args.accepted_trial / 'windows', output / 'hourly' / relative,
                          work / str(day), mask, args.compare_reference))
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for receipt in pool.map(task, tasks):
                report['files'].append(receipt)
                save()
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        save()


if __name__ == '__main__':
    main()
