"""SLURM-only two-generation incremental render/merge test; never transfers files."""
import json
import os
from datetime import date
from pathlib import Path

import build_web_daily_collection as cycle

from hydro_ops.web.globus_transfer import atomic_json


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    if not job.isdecimal():
        raise ValueError('SLURM required')
    work = root / 'web/work/daily-tests' / f'job-{job}'
    work.mkdir(parents=True, exist_ok=False)
    parent = root / 'web/publications/collection-v6-mexico-sm-20251001-20261111'
    reports = []
    for generation in (1, 2):
        output = work / f'generation-{generation}'
        cycle.prepare(parent, output, date(2026, 11, 10), date(2026, 11, 11),
                      {'2026-11-11'} if generation == 2 else set(), [])
        plan = cycle.read(output / 'cycle.json')
        for task in range(plan['tasks']):
            cycle.worker(output, task)
            cycle.worker(output, task)  # Receipt-based repeat must be a no-op.
        cycle.finalize(output)
        report = cycle.read(output / 'publication/acceptance.json')
        reports.append(report)
        if generation == 2:
            if report['reusedImages'] != 8 or report['changedSources'] != 4:
                raise ValueError('Expected one day reused and one day rebuilt in both domains')
            for domain in ('cnrfc', 'ucrb'):
                old = cycle.read(parent / domain / 'availability.json')['assets']
                new = cycle.read(output / 'publication' / domain / 'availability.json')['assets']
                if {r['id']: r['sha256'] for r in old} != {r['id']: r['sha256'] for r in new}:
                    raise ValueError('Forced rebuild differs from accepted prior generation')
        parent = output / 'publication'
    atomic_json(work / 'acceptance.json', {'status': 'passed', 'job_id': job, 'generations': reports,
        'scope': 'Two-domain two-day incremental reuse/rebuild equivalence; no transfer or cron activation.'})
    print(json.dumps({'status': 'passed', 'report': str(work / 'acceptance.json')}))


if __name__ == '__main__':
    main()
