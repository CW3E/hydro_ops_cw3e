#!/usr/bin/env python3
"""Sequential months within one year, parallel disjoint-year repair follow-ups."""
import argparse
import os
from datetime import date
from pathlib import Path

from refresh_rain_repair_summaries import refresh

from hydro_ops.forcing.repair_transaction import atomic_json, sandbox


def month_tasks(year, start, end):
    if start > end or year < start.year or year > end.year or start.day != 1:
        raise ValueError('Invalid year/range (start must be first of month)')
    tasks = []
    for month in range(1, 13):
        day = date(year, month, 1)
        if start <= day <= end:
            last = end if year == end.year and month == end.month else None
            tasks.append((day, last))
    return tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    workspace = sandbox(args.workspace, root)
    workspace.mkdir(parents=True, exist_ok=True)
    report_path = workspace/f'{args.year}-job-{os.environ["SLURM_JOB_ID"]}.json'
    report = {'status': 'running', 'year': args.year, 'months': []}
    try:
        for month, end in month_tasks(args.year, args.start, args.end):
            result = refresh(root, month, workspace/f'{month:%Y%m}', root/'forcing/outputs', end=end)
            report['months'].append({'month': str(month), 'status': result['status'],
                                     'days': len(result['daily']),
                                     'monthly_deferred': result.get('monthly_deferred')})
            atomic_json(report_path, report)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(report_path, report)


if __name__ == '__main__':
    main()
