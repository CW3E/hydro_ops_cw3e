"""Freeze NRT recovery scope, repair March context, then drain eligible revisions."""
import json
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    campaign = root/'forcing/work/revision-backlog-drain/nrt-commissioning-20261108'
    campaign.mkdir(parents=True, exist_ok=False)
    queue = json.loads((root/'forcing/status/prism-revisions/queue.json').read_text())
    keys = sorted(k for k, event in queue['pending'].items() if event['stream'] == 'nrt')
    atomic_json(campaign/'plan.json', {'stream': 'nrt', 'keys': keys, 'maximum_batches': 128,
                                     'retro_publication': 'deferred to protect active model forcing pins',
                                     'cron_changed': False, 'originals_retained': True})
    rows = []
    env = dict(os.environ)
    env.pop('SBATCH_RESERVATION', None)
    def submit(cmd, stage):
        rows.append({'stage': stage, 'status': 'submission_uncertain', 'command': cmd})
        atomic_json(campaign/'submission.json', rows)
        job = subprocess.check_output(['sbatch', '--parsable', *cmd], cwd=root, env=env, text=True).strip().split(';')[0]
        if not job.isdecimal():
            raise ValueError('Unexpected sbatch response')
        rows[-1].update(status='submitted', job_id=job)
        atomic_json(campaign/'submission.json', rows)
        print(json.dumps(rows[-1]), flush=True)
        return job
    submit(['--partition=shared-128', '--nodes=1', '--ntasks=1', '--cpus-per-task=2', '--time=01:00:00',
            '--job-name=prism-revision-backlog-classification', '--output=forcing/logs/revision-backlog-audit-%j.out',
            '--wrap=source bin/project_environment.sh; "$HYDRO_OPS_PYTHON" bin/audit_revision_backlog.py'], 'audit')
    context = submit(['slurm/repair_nrt_revision_context.sh'], 'march_context')
    submit([f'--dependency=afterok:{context}', '--job-name=prism-nrt-backlog-batch-000',
            'slurm/drain_revision_backlog.sh', '--campaign', str(campaign.relative_to(root))], 'nrt_backlog')


if __name__ == '__main__':
    main()
