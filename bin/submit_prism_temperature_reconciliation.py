"""Freeze outstanding PRISM temperature dates and submit a read-only diagnostic."""
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    campaign = root/'forcing/work/temperature-reconciliation'/datetime.now(UTC).strftime('scan-%Y%m%dT%H%M%SZ')
    campaign.mkdir(parents=True, exist_ok=False)
    raw = (root/'forcing/status/prism-revisions/queue.json').read_bytes()
    queue = json.loads(raw)
    days = sorted({key.split('/')[1] for event in queue['pending'].values()
                   for key in event['sources'] if key.startswith(('tmin/', 'tmax/'))})
    if not days:
        raise ValueError('No temperature obligations')
    plan = campaign/'plan.json'
    atomic_json(plan, {'days': days, 'queue_sha256': hashlib.sha256(raw).hexdigest(),
                      'production_modified': False, 'queue_modified': False,
                      'purpose': 'Read-only temperature midpoint diagnostic, not replay'})
    env = dict(os.environ)
    env.pop('SBATCH_RESERVATION', None)
    cmd = ['sbatch', '--parsable', f'--array=0-{len(days)-1}%2',
           '--job-name=prism-temperature-queue-readonly',
           'slurm/audit_prism_temperature_midpoint.sh', '--plan', str(plan)]
    atomic_json(campaign/'submission.json', {'status': 'submission_uncertain', 'command': cmd})
    job = subprocess.check_output(cmd, cwd=root, env=env, text=True).strip().split(';')[0]
    if not job.isdecimal():
        raise ValueError('Unrecognized sbatch response; inspect before retry')
    record = {'status': 'submitted', 'job': job, 'windows': len(days),
              'concurrent_tasks': 2, 'total_allocated_cpus': 128, 'plan': str(plan)}
    atomic_json(campaign/'submission.json', record)
    print(json.dumps(record))


if __name__ == '__main__':
    main()
