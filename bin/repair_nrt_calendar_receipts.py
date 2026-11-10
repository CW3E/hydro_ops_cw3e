"""Repair explicitly listed stale PRISM calendar publications, never bless stale receipts."""
import argparse
import os
from datetime import date
from pathlib import Path

from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.nrt_calendar_publication import publish
from hydro_ops.forcing.nrt_cycle import day_path, identity, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=date.fromisoformat, nargs='+', required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    state = root/'forcing/work/nrt-calendar-receipt-repair'/job
    state.mkdir(parents=True, exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}")
    output = root/'forcing/outputs/conus/nrt/hourly'
    baseline = root/'forcing/outputs/conus/baseline/hourly'
    report = {'status': 'running', 'days': []}
    try:
        for day in args.days:
            path = day_path(output, day)
            old = read_json(path.with_name(path.name+'.nrt-receipt.json'))
            if old.get('status') == 'passed' and old.get('published_identity') == identity(path):
                report['days'].append({'day': str(day), 'status': 'already_current'})
                continue
            result = publish(root, path, path, baseline, day, scratch, state)
            report['days'].append({'day': str(day), 'status': result['status'],
                                  'audit_record_writes': result['audit_record_writes']})
            _atomic_json(state/'progress.json', report)
        report['status'] = 'passed'
    finally:
        if report['status'] == 'running':
            report['status'] = 'failed'
        _atomic_json(state/'acceptance.json', report)
        print(report)


if __name__ == '__main__':
    main()
