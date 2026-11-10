"""Bounded real HRRR/GFS acquisition via the operational latest-hour runner."""
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.nrt_cycle import identity
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    work = root/'forcing/work/latest-acquisition-test'/job
    work.mkdir(parents=True, exist_ok=False)
    cutoff = datetime.now(UTC)-timedelta(hours=3)
    day, hour = cutoff.date(), min(5, cutoff.hour)
    command = [sys.executable, 'bin/run_latest_nrt.py', '--test-workspace', str(work),
               '--test-day', str(day), '--test-end-hour', str(hour)]
    report = {'status': 'running', 'job': job, 'day': str(day), 'requested_end_hour': hour,
              'production_modified': False, 'live_source_acquisition': True}
    try:
        subprocess.run(command, check=True)
        first = json.loads((work/'status/latest-extension.json').read_text())
        atomic_json(work/'first.json', first)
        outputs = list((work/'nrt/hourly').glob('*/*/*.LDASIN_DOMAIN1'))
        if first['status'] != 'passed' or len(outputs) != 1 or not first['days']:
            raise ValueError('No accepted newly acquired hourly output')
        receipts = [json.loads(p.with_name(p.name+'.nrt-receipt.json').read_text()) for p in outputs]
        report['gfs_hours'] = sum(r['gfs_hours'] for r in receipts)
        if not report['gfs_hours']:
            raise ValueError('Required HRRR/GFS scenario not exercised')
        before = {str(p): identity(p) for p in outputs}
        subprocess.run(command, check=True)
        repeat = json.loads((work/'status/latest-extension.json').read_text())
        atomic_json(work/'repeat.json', repeat)
        if repeat['status'] != 'passed' or repeat['days']:
            raise ValueError('Bounded repeat unexpectedly extended data')
        if any(identity(Path(p)) != pin for p, pin in before.items()):
            raise ValueError('Bounded repeat rewrote accepted output')
        report.update(status='passed', latest_hour=first['latest_model_ready_hour'], unchanged_repeat=True)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(work/'acceptance.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
