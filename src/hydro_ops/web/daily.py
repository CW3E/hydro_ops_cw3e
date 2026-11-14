"""Job-specific input gates for the web daily coordinator."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .globus_transfer import digest


def identity(path):
    stat = path.stat()
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns]


def validate_inputs(root, request):
    """Check publication, not just SLURM completion; return receipts to pin."""
    root = Path(root).resolve()
    first, last = date.fromisoformat(request['start']), date.fromisoformat(request['end'])
    if first > last or set(request['jobs']) != {'cnrfc', 'ucrb'}:
        raise ValueError('Expected valid interval and both web domains')
    pins = {}

    def read(path):
        path = path.resolve()
        relative = str(path.relative_to(root))
        if path.name == 'latest.json':
            raise ValueError('Mutable latest receipt forbidden')
        result = json.loads(path.read_text())
        pins[relative] = digest(path)
        return result

    for domain, jobs in request['jobs'].items():
        if set(jobs) != {'forcing', 'model'} or not all(str(j).isdecimal() for j in jobs.values()):
            raise ValueError('Explicit numeric forcing and model job IDs required')
        forcing_id, model_id = str(jobs['forcing']), str(jobs['model'])
        forcing_path = root / f'forcing/status/{domain}' / (
            f'sync-{forcing_id}/status.json' if domain == 'cnrfc' else f'nrt-catchup-{forcing_id}/status.json')
        forcing = read(forcing_path)
        if (forcing.get('status') != 'passed' or str(forcing.get('job_id')) != forcing_id
                or forcing.get('failed') != 0 or forcing.get('total', 0) <= 0
                or forcing.get('published', 0) + forcing.get('skipped', 0) != forcing['total']):
            raise ValueError('Forcing subset publication incomplete')
        if domain == 'ucrb' and (forcing.get('domain') != domain or forcing.get('source_domain') != 'conus'):
            raise ValueError('Wrong forcing domain')
        model_path = root / f'nwm/runs/{domain}/nrt/cycles/job-{model_id}/cycle.json'
        model = read(model_path)
        if (model.get('status') != 'complete' or model.get('test') is not False
                or str(model.get('job_id')) != model_id or model.get('domain') != domain
                or model.get('rst_typ') != 1 or model.get('source_revisions_after_snapshot')):
            raise ValueError('Model publication incomplete or stale')
        start, stop = datetime.fromisoformat(model['start']), datetime.fromisoformat(model['end'])
        if start.time().isoformat() != '00:00:00' or stop.time().isoformat() != '00:00:00':
            raise ValueError('Model receipt not midnight bounded')
        if start.date() > first or stop.date() <= last:
            raise ValueError('Requested refresh interval outside model receipt coverage')
        journal_path = model_path.parent / 'publication/transaction.json'
        if Path(model['journal']).resolve() != journal_path.resolve():
            raise ValueError('Unexpected model publication journal')
        journal = read(journal_path)
        if journal.get('status') != 'complete' or not journal.get('files') or len(journal['files']) != model['published_files']:
            raise ValueError('Model publication transaction incomplete')
        targets = {r['target']: r for r in journal['files']}
        current = first
        while current <= last:
            forcing_file = root / f'forcing/outputs/{domain}/nrt/daily/{current:%Y/%m/%Y%m%d}.LDASIN_DOMAIN1.daily'
            subset = read(forcing_file.with_name(forcing_file.name + '.subset.json'))
            if (subset.get('status') != 'passed' or subset.get('output_identity') != identity(forcing_file)
                    or Path(subset['output']).resolve() != forcing_file.resolve()
                    or subset.get('source_identity') != identity(Path(subset['source']))):
                raise ValueError('Forcing daily summary receipt stale')
            model_file = root / f'nwm/outputs/{domain}/nrt/daily/{current:%Y/%m/%Y%m%d}.LDASOUT_DOMAIN1.daily'
            record = targets.get(str(model_file))
            if not record or record['after'] != identity(model_file):
                raise ValueError('Model daily output not the accepted job version')
            current += timedelta(days=1)
    return pins


def dependency_command(root, request_path, gate_job):
    if not str(gate_job).isdecimal():
        raise ValueError('Numeric gate job ID required')
    return ['sbatch', '--parsable', '--dependency=afterok:' + str(gate_job),
            '--kill-on-invalid-dep=yes', f'--chdir={root}',
            '--job-name=web-daily-receipt-gated-render-plan-test',
            str(root / 'slurm/test_web_daily_coordinator.sh'), 'run', '--request', str(request_path),
            '--gate-job', str(gate_job)]
