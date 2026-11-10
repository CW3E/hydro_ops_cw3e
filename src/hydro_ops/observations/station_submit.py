"""Route routine station jobs to the active collection, never to legacy writers."""
import fcntl
import json
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json

from .station_operations import STATE, active_catalog


def submit(root, selection, *, dry_run=False, resume_report=None, continuations=0, after_job=None):
    root = Path(root).resolve()
    if not 0 <= continuations <= 3:
        raise ValueError('At most three continuation jobs allowed')
    if after_job is not None and (not str(after_job).isdecimal() or str(after_job) != os.environ.get('SLURM_JOB_ID')):
        raise ValueError('Continuation must follow the current SLURM job')
    if active_catalog(root) is None:
        raise ValueError('Station collection not active')
    state = root/STATE
    name = f'obs-stations-{selection}'
    with (state/'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lines = subprocess.check_output(['squeue', '--me', '--noheader', '--format=%i|%j'], text=True).splitlines()
        active = [line.split('|') for line in lines if '|obs-stations-' in line
                  and line.split('|')[0] != after_job]
        if any(jobname == name for _, jobname in active):
            return {'status': 'skipped', 'reason': 'same selection already queued'}
        cmd = ['sbatch', '--parsable', f'--chdir={root}', f'--job-name={name}']
        dependencies = [job for job,_ in active]+([after_job] if after_job else [])
        if dependencies:
            cmd.append('--dependency=afterany:'+':'.join(dependencies))
        cmd.extend(['slurm/update_station_collection.sh', '--selection', selection])
        if resume_report is not None:
            report = Path(resume_report).resolve()
            if not report.is_relative_to(root/'observations/work/station-cycles'):
                raise ValueError('Resume report must belong to a station cycle')
            if json.loads(report.read_text()).get('selection') != selection:
                raise ValueError('Resume selection mismatch')
            cmd.extend(['--resume-report',str(report)])
        if continuations:
            cmd.extend(['--continuations',str(continuations)])
        if dry_run:
            return {'status': 'planned', 'command': cmd}
        ledger = state/f'{selection}-submission.json'
        if ledger.exists() and json.loads(ledger.read_text())['status'] == 'submission_uncertain':
            raise ValueError('Review uncertain station submission')
        atomic_json(ledger, {'status': 'submission_uncertain', 'command': cmd})
        env = dict(os.environ)
        env.pop('SBATCH_RESERVATION', None)
        job = subprocess.check_output(cmd, cwd=root, env=env, text=True).strip().split(';')[0]
        if not job.isdecimal():
            raise ValueError('Unexpected sbatch response')
        result = {'status': 'submitted', 'job_id': job, 'command': cmd}
        atomic_json(ledger, result)
        return result
