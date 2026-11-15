"""Route routine station jobs to the active collection, never to legacy writers."""
import fcntl
import json
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json

from .station_cohorts import scope_name
from .station_operations import STATE, active_catalog


def submit(root, selection, *, dry_run=False, resume_report=None, continuations=0, after_job=None,
           prepare_workers=None, limit=None, after_success=None, representative=False,
           swe_resolutions=('daily', 'monthly'), optimized_coordinator=False, usgs_scope='all'):
    root = Path(root).resolve()
    status_name = scope_name(selection, usgs_scope)
    if not swe_resolutions or set(swe_resolutions) - {'daily', 'monthly', 'hourly', 'event'}:
        raise ValueError('Invalid SWE resolutions')
    if selection == 'cdec-swe' and set(swe_resolutions) - {'daily', 'monthly'}:
        raise ValueError('CDEC SWE policy permits only Snow Course monthly and Snow Pillow daily')
    if prepare_workers is None:
        prepare_workers = 4 if selection == 'usgs' else 0
    if prepare_workers not in (0, 1, 4) or (prepare_workers and continuations):
        raise ValueError('Invalid or uncommissioned preparation configuration')
    if optimized_coordinator and not prepare_workers:
        raise ValueError('Optimized coordinator requires preparation workers')
    if limit is not None and (limit < 1 or resume_report or continuations):
        raise ValueError('Positive commissioning limit requires a fresh bounded cycle')
    if representative and (limit is not None or resume_report or continuations):
        raise ValueError('Representative commissioning requires a fresh bounded cycle')
    if after_success is not None and (not str(after_success).isdecimal() or after_job is not None):
        raise ValueError('Successful predecessor must be a numeric job, separate from continuation')
    if not 0 <= continuations <= 3:
        raise ValueError('At most three continuation jobs allowed')
    if after_job is not None and (not str(after_job).isdecimal() or str(after_job) != os.environ.get('SLURM_JOB_ID')):
        raise ValueError('Continuation must follow the current SLURM job')
    if active_catalog(root) is None:
        raise ValueError('Station collection not active')
    state = root/STATE
    name = f'obs-stations-{status_name}'
    with (state/'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lines = subprocess.check_output(['squeue', '--me', '--noheader', '--format=%i|%j'], text=True).splitlines()
        active = [line.split('|') for line in lines if '|obs-stations-' in line
                  and line.split('|')[0] != after_job]
        if any(jobname == name and job != str(after_success) for job, jobname in active):
            return {'status': 'skipped', 'reason': 'same selection already queued'}
        cmd = ['sbatch', '--parsable', f'--chdir={root}', f'--job-name={name}']
        dependencies = [job for job,_ in active if job != str(after_success)]+([after_job] if after_job else [])
        clauses = []
        if dependencies:
            clauses.append('afterany:'+':'.join(dependencies))
        if after_success is not None:
            clauses.append('afterok:'+str(after_success))
            cmd.append('--kill-on-invalid-dep=yes')
        if clauses:
            cmd.append('--dependency='+','.join(clauses))
        cmd.extend(['slurm/update_station_collection.sh', '--selection', selection])
        if usgs_scope != 'all':
            cmd.extend(['--usgs-scope', usgs_scope])
        if selection == 'cdec-swe':
            cmd.extend(['--swe-resolutions', *sorted(set(swe_resolutions))])
        if prepare_workers or selection == 'usgs':
            cmd.extend(['--prepare-workers', str(prepare_workers)])
        if optimized_coordinator:
            cmd.append('--optimized-coordinator')
        if limit is not None:
            cmd.extend(['--limit', str(limit)])
        if representative:
            cmd.append('--representative')
        if resume_report is not None:
            report = Path(resume_report).resolve()
            if not report.is_relative_to(root/'observations/work/station-cycles'):
                raise ValueError('Resume report must belong to a station cycle')
            saved = json.loads(report.read_text())
            if saved.get('selection') != selection or saved.get('usgs_scope', 'all') != usgs_scope:
                raise ValueError('Resume selection mismatch')
            cmd.extend(['--resume-report',str(report)])
        if continuations:
            cmd.extend(['--continuations',str(continuations)])
        if dry_run:
            return {'status': 'planned', 'command': cmd}
        ledger = state/f'{status_name}-submission.json'
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
