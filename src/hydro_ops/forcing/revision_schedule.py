"""Schedule bounded PRISM replay after normal publication, never enable cron."""
import os
import subprocess
from pathlib import Path


def commands(cycle, stream, partition, account, dependencies):
    if (cycle, stream) not in (('daily', 'nrt'), ('monthly-retro', 'retro')):
        raise ValueError('Revision replay belongs to daily NRT or monthly Retro only')
    if not dependencies or any(not str(d).isdecimal() for d in dependencies):
        raise ValueError('Successful numeric upstream job dependencies required')
    base = ['sbatch', '--parsable', f'--partition={partition}']
    if account:
        base.append(f'--account={account}')
    scanner = [*base, '--dependency=afterok:'+':'.join(dict.fromkeys(map(str, dependencies))),
               f'--job-name=nwm-cycle-{cycle}-revision-scan', 'slurm/queue_prism_revisions.sh']
    worker = [*base, f'--job-name=nwm-cycle-{cycle}-revision-replay',
              'slurm/run_bounded_revision_queue.sh', '--stream', stream,
              '--routine', '--reuse-baselines', '--limit', '2', '--execute', '--publish']
    return scanner, worker


def submit(root, *, cycle, stream, partition, account, dependencies):
    scanner, worker = commands(cycle, stream, partition, account, dependencies)
    env = {k: v for k, v in os.environ.items() if k != 'SBATCH_RESERVATION'}
    def launch(command):
        job = subprocess.check_output(command, cwd=Path(root), text=True, env=env).strip().split(';')[0]
        if not job.isdecimal():
            raise RuntimeError(f'Unexpected sbatch response: {job!r}')
        return job
    scan_id = launch(scanner)
    worker.insert(1, f'--dependency=afterok:{scan_id}')
    return {'scan_job_id': scan_id, 'replay_job_id': launch(worker),
            'maximum_dates': 2, 'policy': 'bounded_existing_products',
            'baseline_reuse': 'exact_input_accepted_only',
            'backlog_completion_required': False}
