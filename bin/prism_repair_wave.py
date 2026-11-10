#!/usr/bin/env python3
"""Prepare frozen original inputs and run the isolated eight-month scaling wave."""
import argparse
import calendar
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from hydro_ops.forcing.rain_repair_writer import digest
from hydro_ops.forcing.repair_transaction import (
    atomic_json,
    check_snapshot,
    create_snapshot,
    sandbox,
)


def month_tasks(start, end_month, end_date=None):
    if start.day != 1 or end_month.day != 1 or start > end_month:
        raise ValueError('Month bounds must be ordered first-of-month dates')
    last = end_month + timedelta(days=calendar.monthrange(end_month.year, end_month.month)[1]-1)
    if end_date is not None:
        if not end_month <= end_date <= last:
            raise ValueError('End date must lie in the final month')
        last = end_date
    tasks = []
    month = start
    while month <= last:
        days = min(calendar.monthrange(month.year, month.month)[1], (last-month).days+1)
        tasks.append({'start': str(month), 'days': days})
        month += timedelta(days=days)
    return tasks, last + timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'summarize'))
    parser.add_argument('--wave', type=Path, required=True)
    parser.add_argument('--array-job')
    parser.add_argument('--previous-wave', type=Path)
    parser.add_argument('--previous-array-job')
    parser.add_argument('--start-month', type=date.fromisoformat, default=date(1981, 1, 1))
    parser.add_argument('--end-month', type=date.fromisoformat, default=date(1981, 8, 1))
    parser.add_argument('--end-date', type=date.fromisoformat, help='Optional inclusive partial final month endpoint')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    wave = sandbox(args.wave, root)
    if args.action == 'prepare':
        months, month = month_tasks(args.start_month, args.end_month, args.end_date)
        wave.mkdir(parents=True, exist_ok=False)
        start, end = args.start_month - timedelta(days=1), month
        dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        overrides = {}
        previous_window = None
        if args.previous_wave:
            previous = sandbox(args.previous_wave,root)
            prior = json.loads((previous/'plan.json').read_text())
            audit = json.loads((previous/'acceptance.json').read_text())
            if audit['status'] != 'passed' or not args.previous_array_job or not args.previous_array_job.isdigit():
                raise ValueError('Accepted preceding wave and numeric array ID required')
            last = prior['months'][-1]
            if date.fromisoformat(last['start'])+timedelta(days=last['days']) != args.start_month:
                raise ValueError('Preceding wave is not adjacent')
            check_snapshot(prior['snapshot'],previous/'originals')
            overrides[start] = previous/'originals'/start.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            prior_dir = root/'forcing/work/prism-rain-pipeline'/f'{args.previous_array_job}_{len(prior["months"])-1}'
            if json.loads((prior_dir/'acceptance.json').read_text())['status'] != 'passed':
                raise ValueError('Preceding candidate month not accepted')
            previous_window = str(prior_dir/args.start_month.strftime('windows/%Y/%m/%Y%m%d.LDASIN_DOMAIN1'))
            if not Path(previous_window).exists():
                raise FileNotFoundError(previous_window)
        elif args.previous_array_job:
            parser.error('--previous-array-job requires --previous-wave')
        create_snapshot(root / 'forcing/outputs/conus/retro/hourly', wave / 'originals', dates, root,
                        source_overrides=overrides)
        paths = [root / 'bin/prism_repair_wave.py', root / 'bin/trial_prism_rain_pipeline.py',
                 root / 'bin/benchmark_prism_rain_writer.py',
                 *sorted((root / 'src/hydro_ops/forcing').glob('prism*.py')),
                 root / 'src/hydro_ops/forcing/rain_repair_writer.py',
                 root / 'src/hydro_ops/forcing/repair_transaction.py']
        atomic_json(wave / 'plan.json', {'status': 'prepared', 'production_modified': False,
                    'snapshot': str(wave / 'originals/snapshot.json'),
                    'previous_window': previous_window,
                    'code_sha256': {str(p.relative_to(root)): digest(p) for p in paths},
                    'months': months})
        return
    plan = json.loads((wave / 'plan.json').read_text())
    if plan['status'] != 'prepared':
        raise ValueError('Wave not prepared')
    for name, checksum in plan['code_sha256'].items():
        if digest(root / name) != checksum:
            raise ValueError(f'Code changed after wave preparation: {name}')
    check_snapshot(plan['snapshot'], wave / 'originals')
    if args.action == 'summarize':
        import numpy as np
        from netCDF4 import Dataset
        if not args.array_job or not args.array_job.isdigit():
            parser.error('Summary requires numeric --array-job')
        reports, directories = [], []
        for i, month in enumerate(plan['months']):
            directory = root / 'forcing/work/prism-rain-pipeline' / f'{args.array_job}_{i}'
            report = json.loads((directory / 'acceptance.json').read_text())
            if report['status'] != 'passed' or len(report['files']) != month['days']:
                raise ValueError(f'Missing/failed/incomplete month {month}')
            directories.append(directory)
            reports.append({'task': i, 'start': month['start'], 'days': month['days'],
                            'elapsed_seconds': report['elapsed_seconds'],
                            'window_stage_seconds': report['window_stage_seconds'],
                            'writer_stage_seconds': report['writer_stage_seconds']})
        boundaries = []
        for i in range(1, len(plan['months'])):
            label = date.fromisoformat(plan['months'][i]['start'])
            relative = label.strftime('windows/%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            with Dataset(directories[i-1] / relative) as left, Dataset(directories[i] / relative) as right:
                for name in ('RAINRATE', 'prism_timing_source_id'):
                    for h in range(24):
                        a, b = left[name][h], right[name][h]
                        np.testing.assert_array_equal(np.ma.getmaskarray(a), np.ma.getmaskarray(b))
                        np.testing.assert_array_equal(a.compressed(), b.compressed())
            boundaries.append({'day': str(label), 'shared_window_exact': True})
        preceding = None
        if plan.get('previous_window'):
            label = date.fromisoformat(plan['months'][0]['start'])
            right_path = directories[0]/label.strftime('windows/%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            with Dataset(plan['previous_window']) as left, Dataset(right_path) as right:
                for name in ('RAINRATE','prism_timing_source_id'):
                    for h in range(24):
                        a,b = left[name][h],right[name][h]
                        np.testing.assert_array_equal(np.ma.getmaskarray(a),np.ma.getmaskarray(b))
                        np.testing.assert_array_equal(a.compressed(),b.compressed())
            preceding = {'day':str(label),'shared_window_exact':True}
        accounting = subprocess.check_output(
            ['sacct', '--array', '-X', '-j', args.array_job, '-n', '-P',
             '--format=JobID,State,Start,End,Elapsed,AllocCPUS'], text=True)
        atomic_json(wave / 'acceptance.json', {'status': 'passed', 'production_modified': False,
                    'array_job': args.array_job, 'preceding_boundary': preceding,
                    'months': reports, 'boundaries': boundaries, 'slurm_accounting': accounting,
                    'originals_retained': True, 'candidate_files': sum(r['days'] for r in reports)})
        return
    task = plan['months'][int(os.environ['SLURM_ARRAY_TASK_ID'])]
    subprocess.run([sys.executable, 'bin/trial_prism_rain_pipeline.py',
                    '--original-root', str(wave / 'originals'),
                    '--snapshot-manifest', plan['snapshot'], '--start', task['start'],
                    '--days', str(task['days']), '--window-workers', '2', '--writer-workers', '2'],
                   check=True)


if __name__ == '__main__':
    main()
