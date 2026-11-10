#!/usr/bin/env python3
"""Isolated end-to-end rain repair using compact windows and copy/replace output."""
import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from benchmark_prism_rain_writer import task as publish_day

from hydro_ops.forcing.prism_support_production import candidate_path, run
from hydro_ops.forcing.repair_transaction import check_snapshot


def archive(root, day):
    return root / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')


def window_task(options):
    root, original, windows, work, day = options
    work.mkdir(parents=True, exist_ok=False)
    previous = day - timedelta(days=1)
    args = argparse.Namespace(
        hours=[archive(original, previous)] * 12 + [archive(original, day)] * 12,
        hour_indices=list(range(12, 24)) + list(range(12)),
        daily_output=archive(windows, day), day=day,
        diagnostics=windows / day.strftime('%Y/%m/%Y%m%d.prism_precipitation_diagnostics.nc'),
        prism=root / 'forcing/inputs/oregon_state/prism/an/4km/daily/ppt' /
        day.strftime('%Y/%m/prism_ppt_us_25m_%Y%m%d.nc'),
        prism_variable='ppt',
        weights=root / 'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc',
        active_mask=root / 'nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc',
        nldas_root=root / 'forcing/inputs/nasa/nldas2/fora0125_hourly_v2.0',
        weak_support_threshold=.1, direct_fallback=True, bounded_inactive_fallback=False,
        compact_rain_window=True, temperature_corrected_day=None,
        max_iterations=200, work_directory=work, revision='stable')
    if run(args) != 0:
        raise ValueError(f'Window rejected: {day}')
    report_path = args.diagnostics.with_suffix('.json')
    report = json.loads(report_path.read_text())
    if report['status'] != 'published_candidate':
        raise ValueError(f'Window not published: {day}')
    return {'day': str(day), 'report': str(report_path),
            'acceptance_class': report['acceptance_class'],
            'phase_seconds': report['phase_seconds'],
            'elapsed_seconds': report['elapsed_seconds']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-root', type=Path, required=True)
    parser.add_argument('--accepted-trial', type=Path)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--days', type=int, default=4)
    parser.add_argument('--window-workers', type=int, default=2)
    parser.add_argument('--writer-workers', type=int, default=2)
    parser.add_argument('--compare-reference', action='store_true')
    parser.add_argument('--snapshot-manifest', type=Path)
    args = parser.parse_args()
    if not 1 <= args.days <= 31 or any(not 1 <= n <= 2 for n in
                                     (args.window_workers, args.writer_workers)):
        parser.error('Require 1–31 days and 1–2 workers per stage')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    if args.snapshot_manifest:
        check_snapshot(args.snapshot_manifest, args.original_root)
    if args.compare_reference and args.accepted_trial is None:
        parser.error('--compare-reference requires --accepted-trial')
    days = [args.start + timedelta(days=i) for i in range(args.days)]
    if args.accepted_trial is not None:
        if (args.accepted_trial/'RETIRED.json').exists():
            raise ValueError('Reference pipeline retired; generate a new reference campaign')
        accepted = json.loads((args.accepted_trial / 'acceptance.json').read_text())
        if accepted['status'] != 'passed' or not accepted['rain_only']:
            raise ValueError('Require accepted rain-only reference')
        reference_paths = {Path(f['path']).resolve() for f in accepted['files']}
        for day in days:
            if archive(args.accepted_trial / 'hourly', day).resolve() not in reference_paths:
                raise ValueError(f'Unaccepted reference date: {day}')
    job = (f"{os.environ['SLURM_ARRAY_JOB_ID']}_{os.environ['SLURM_ARRAY_TASK_ID']}"
           if 'SLURM_ARRAY_JOB_ID' in os.environ else os.environ['SLURM_JOB_ID'])
    output = candidate_path(root / 'forcing/work/prism-rain-pipeline' / job, root)
    output.mkdir(parents=True, exist_ok=False)
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}/rain-pipeline")
    windows = output / 'windows'
    report = {'status': 'running', 'production_modified': False, 'scientific_validation': False,
              'window_workers': args.window_workers, 'writer_workers': args.writer_workers,
              'reference_trial': str(args.accepted_trial) if args.accepted_trial else None,
              'snapshot_manifest': str(args.snapshot_manifest) if args.snapshot_manifest else None,
              'compare_reference': args.compare_reference, 'windows': [], 'files': []}
    started = time.monotonic()

    def save():
        part = output / 'acceptance.json.part'
        part.write_text(json.dumps(report, indent=2) + '\n')
        part.replace(output / 'acceptance.json')

    save()
    try:
        tasks = [(root, args.original_root, windows, work / 'windows' / str(day), day)
                 for day in [*days, days[-1] + timedelta(days=1)]]
        with ProcessPoolExecutor(max_workers=args.window_workers) as pool:
            for future in as_completed([pool.submit(window_task, t) for t in tasks]):
                report['windows'].append(future.result())
                save()
        report['window_stage_seconds'] = time.monotonic() - started
        tick = time.monotonic()
        mask = root / 'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
        tasks = [(day, archive(args.original_root, day),
                  archive(args.accepted_trial / 'hourly', day) if args.accepted_trial else None, windows,
                  archive(output / 'hourly', day), work / 'calendar' / str(day), mask,
                  args.compare_reference) for day in days]
        with ProcessPoolExecutor(max_workers=args.writer_workers) as pool:
            for future in as_completed([pool.submit(publish_day, t) for t in tasks]):
                report['files'].append(future.result())
                save()
        report['writer_stage_seconds'] = time.monotonic() - tick
        if args.snapshot_manifest:
            check_snapshot(args.snapshot_manifest, args.original_root)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        save()


if __name__ == '__main__':
    main()
