"""One receipt-gated web follower per CDEC acquisition job, including partial cycles."""
import json
import os
import subprocess
from pathlib import Path

from .globus_transfer import atomic_json, lock


def submit(root, selection, acquisition):
    root = Path(root).resolve()
    settings = root/'config/web_stations.json'
    if selection not in ('cdec-fnf', 'cdec-swe') or not settings.exists():
        return {'status': 'not_configured'}
    config = json.loads(settings.read_text())['products'][selection]
    if not config['enabled']:
        return {'status': 'disabled'}
    if acquisition.get('status') != 'submitted':
        return {'status': 'no_new_acquisition'}
    job = str(acquisition['job_id'])
    if not job.isdecimal() or config['mode'] not in ('publish', 'plan-only'):
        raise ValueError('Invalid station web scheduling request')
    folder = root/'web/status/stations/submissions'/selection/job
    folder.mkdir(parents=True, exist_ok=True)
    with lock(folder/'submission.lock'):
        receipt = folder/'submission.json'
        if receipt.exists():
            return {'status': 'already_recorded', 'record': str(receipt.relative_to(root))}
        command = ['sbatch', '--parsable', f'--chdir={root}', '--dependency=afterany:'+job,
                   '--kill-on-invalid-dep=yes', '--job-name=web-'+selection+'-after-'+job,
                   'slurm/update_web_stations.sh', '--selection', selection,
                   '--report', f'observations/work/station-cycles/{job}/acceptance.json']
        if config['mode'] == 'publish':
            command += ['--publish', '--retry-failed']
        atomic_json(receipt, {'status': 'submission_uncertain', 'command': command})
        env = dict(os.environ)
        env.pop('SBATCH_RESERVATION', None)
        result = subprocess.check_output(command, cwd=root, env=env, text=True).strip().split(';')[0]
        if not result.isdecimal():
            raise ValueError('Invalid web follower job ID')
        record = {'status': 'submitted', 'job_id': result, 'command': command}
        atomic_json(receipt, record)
        return record
