"""Fail-closed daily web scheduling; initially render/plan only."""
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .globus_transfer import atomic_json, lock
from .publication import destination_key


def jobs_from_cycle(state):
    """Use jobs submitted by this cycle, never mutable latest job lookups."""
    if state.get('skip_model_runs'):
        raise ValueError('Model runs disabled')
    models = {'cnrfc': state.get('cnrfc_model_cycle', {}),
              'ucrb': state.get('subdomain_model_cycles', {}).get('ucrb', {})}
    forcing = {'cnrfc': state.get('cnrfc_sync_job_id'),
               'ucrb': state.get('subdomain_sync_jobs', {}).get('ucrb')}
    jobs = {}
    for domain, model in models.items():
        if model.get('status') != 'submitted':
            raise ValueError('Both models must be submitted by this cycle')
        pair = {'forcing': str(forcing[domain]), 'model': str(model.get('job_id'))}
        if not all(v.isdecimal() for v in pair.values()):
            raise ValueError('Numeric job IDs required')
        jobs[domain] = pair
    return jobs


def request_from_receipts(root, jobs):
    """Select the common replay interval and latest accepted full web history."""
    root = Path(root)
    if set(jobs) != {'cnrfc', 'ucrb'}:
        raise ValueError('Both web domains required')
    starts, ends = [], []
    for domain, pair in jobs.items():
        if set(pair) != {'forcing', 'model'} or not all(str(v).isdecimal() for v in pair.values()):
            raise ValueError('Numeric exact job IDs required')
        model = json.loads((root / f'nwm/runs/{domain}/nrt/cycles/job-{pair["model"]}/cycle.json').read_text())
        starts.append(datetime.fromisoformat(model['start']).date())
        ends.append(datetime.fromisoformat(model['end']).date() - timedelta(days=1))
    first, last = max(starts), min(ends)
    if first > last:
        raise ValueError('No common model replay interval')
    config = json.loads((root / 'config/web_globus.json').read_text())
    control = root / 'web/status/deployments' / destination_key(config)
    if (control / 'owner.json').exists():
        raise ValueError('Live publication still in progress')
    accepted = json.loads((control / 'accepted.json').read_text())
    plan = json.loads((root / accepted['plan']).read_text())
    if plan['id'] != accepted['id']:
        raise ValueError('Accepted deployment identity mismatch')
    base = plan['source']
    (root / base).resolve().relative_to(root.resolve())
    return {'test': True, 'start': str(first), 'end': str(last), 'jobs': jobs,
            'base': base, 'history_base': base, 'acceptedDeployment': accepted['id']}


def submit_if_enabled(root, state, cycle_id):
    config = json.loads((root / 'config/web_daily.json').read_text())
    if not config.get('enabled'):
        return {'status': 'disabled'}
    if config.get('mode') not in ('plan-only', 'publish'):
        raise ValueError('Unsupported web daily mode')
    if config['mode'] == 'publish' and not json.loads((root / 'config/web_globus.json').read_text()).get('production_enabled'):
        raise ValueError('Live transfer is disabled')
    if state.get('cycle') != 'daily' or state.get('stream') != 'nrt':
        return {'status': 'not_daily_nrt'}
    try:
        jobs = jobs_from_cycle(state)
    except ValueError as error:
        return {'status': 'deferred_missing_current_cycle_models', 'reason': str(error)}
    if not str(cycle_id).isdecimal():
        raise ValueError('Numeric cycle ID required')
    folder = root / 'web/status/daily/submissions' / str(cycle_id)
    folder.mkdir(parents=True, exist_ok=True)
    with lock(folder / 'submission.lock'):
        receipt = folder / 'submission.json'
        if receipt.exists():
            return {'status': 'already_recorded', 'record': str(receipt.relative_to(root))}
        request = folder / 'jobs.json'
        atomic_json(request, jobs)
        dependencies = list(dict.fromkeys([str(cycle_id)] + [v for p in jobs.values() for v in p.values()]))
        command = ['sbatch', '--parsable', '--dependency=afterok:' + ':'.join(dependencies),
            '--kill-on-invalid-dep=yes', f'--chdir={root}',
            str(root / 'slurm/run_web_daily_plan.sh'), '--jobs', str(request), '--mode', config['mode']]
        atomic_json(receipt, {'status': 'submitting', 'command': command})
        try:
            job = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
            if not job.isdecimal():
                raise ValueError('Invalid sbatch response')
        except BaseException:
            atomic_json(receipt, {'status': 'submission_uncertain', 'command': command})
            raise
        result = {'status': 'submitted', 'job_id': job, 'mode': config['mode'], 'command': command}
        atomic_json(receipt, result)
        return result
