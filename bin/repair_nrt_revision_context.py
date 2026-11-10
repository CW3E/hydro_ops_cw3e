"""Restore the missing March 1 boundary and complete March NRT summary context."""
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from backfill_forcing_summaries import audited
from run_bounded_revision_queue import missing_publication_context

from hydro_ops.forcing.nrt_cycle import run_cycle
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/march-context')
    work.mkdir(parents=True, exist_ok=True)
    report_path = root/f'forcing/work/nrt-march-context/{job}/acceptance.json'
    report = {'status': 'running', 'job': job, 'stream': 'nrt', 'stages': []}
    base = root/'forcing/outputs/conus/nrt'
    boundary = base/'hourly/2026/03/20260301.LDASIN_DOMAIN1'
    try:
        if not boundary.exists():
            result = run_cycle(root, work, date(2026, 3, 1), date(2026, 3, 1), datetime.now(UTC),
                               pipeline='serial')
            if result['status'] != 'passed':
                raise ValueError(f'March 1 production failed: {result.get("errors")}')
        audited(boundary, 'nrt')
        report['stages'].append('march1_hourly_accepted')
        def run(script, *args):
            subprocess.run([sys.executable, 'bin/'+script, *map(str, args)], cwd=root, check=True)
        run('backfill_forcing_summaries.py', '--input-root', base/'hourly', '--output-root', base,
            '--start', '2026-03-01', '--end', '2026-03-31', '--stream', 'nrt', '--workers', '8')
        report['stages'].append('march_daily_monthly_accepted')
        run('backfill_cnrfc_forcing.py', '--stream', 'nrt', '--frequency', 'all', '--incremental',
            '--workers', '8', '--scratch', work/'cnrfc')
        for domain in ('b120', 'ucrb', 'yampa'):
            run('sync_subdomain_nrt_forcing.py', '--domain', domain, '--workers', '8')
        report['stages'].append('four_domain_sync_passed')
        for day in (date(2026, 3, 2), date(2026, 3, 31), date(2026, 4, 1)):
            missing = missing_publication_context(root, 'nrt', day)
            if missing:
                raise ValueError(f'{day} context still missing: {missing}')
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(report_path, report)


if __name__ == '__main__':
    main()
