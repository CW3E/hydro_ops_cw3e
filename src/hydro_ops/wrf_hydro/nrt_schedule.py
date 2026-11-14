"""Configured once-per-UTC-day model submission after successful forcing dependencies."""
import fcntl
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime

from hydro_ops.wrf_hydro.production import write_json


def command(root, dependencies, rewind_days, domain='cnrfc', mpi_ranks=64):
    if domain not in ('cnrfc', 'b120', 'yampa', 'ucrb') or mpi_ranks not in (32, 64):
        raise ValueError('Unsupported daily model domain/rank selection')
    if not dependencies or not all(re.fullmatch(r'\d+', str(j)) for j in dependencies):
        raise ValueError('Explicit successful forcing/subset dependencies are required')
    if not isinstance(rewind_days, int) or rewind_days < 1:
        raise ValueError('Positive rewind required')
    args = ['sbatch', '--parsable', '--dependency=afterok:' + ':'.join(dict.fromkeys(map(str, dependencies))),
            f'--job-name=wrfh-{domain}-nrt-daily-replay-{mpi_ranks}mpi', f'--chdir={root}']
    if domain != 'cnrfc':
        args += [f'--ntasks={mpi_ranks}', '--kill-on-invalid-dep=yes',
                 f'--output={root}/nwm/logs/{domain}-daily-cycle-%j.out']
    args += [
            str(root / 'slurm/run_cnrfc_daily_cycle.sh'), '--rewind-days', str(rewind_days),
            '--snapshot-forcing']
    if domain != 'cnrfc':
        args += ['--domain', domain]
    return args


def require_nrt_publication(root, domain, config=None):
    """UCRB was published by thermal refresh, not the old directory promotion."""
    if domain != 'ucrb':
        promotion = json.loads((root / f'nwm/status/{domain}/nrt-promotion/promotion.json').read_text())
        if promotion.get('status') != 'complete':
            raise ValueError('Complete NRT promotion required')
        return
    try:
        config = config or json.loads((root/'config/ucrb_nrt_daily.json').read_text())
        path = (root/config['canonical_acceptance']).resolve()
        expected = (root/'nwm/status/thermal_refresh_2026_v1/ucrb/nrt/acceptance.json').resolve()
        if path != expected:
            raise ValueError('Unexpected canonical UCRB acceptance')
        report = json.loads(path.read_text())
        transaction = json.loads((path.parent/'publication/transaction.json').read_text())
        gate = json.loads((path.parent/'gate.json').read_text())
        plan_bytes = (root/'nwm/status/thermal_refresh_2026_v1/plan.json').read_bytes()
        plan = json.loads(plan_bytes)
        manifest = json.loads((root/'nwm/static/domains/ucrb/manifest.json').read_text())
        if (report.get('status') != 'complete' or report.get('domain') != domain
                or report.get('stream') != 'nrt' or report.get('end') != '2026-11-08 00:00:00'
                or transaction.get('status') != 'complete' or not transaction.get('files')
                or len(transaction['files']) != report.get('published_files')
                or gate.get('status') != 'passed' or gate.get('domain') != domain
                or gate.get('restart_equivalence') != 'passed'
                or gate.get('plan_sha256') != report.get('plan_sha256')
                or report.get('plan_sha256') != hashlib.sha256(plan_bytes).hexdigest()
                or plan['domains']['ucrb']['mask_sha256'] != manifest['mask_sha256']):
            raise ValueError('UCRB canonical refresh publication not accepted')
    except (OSError, KeyError) as error:
        raise ValueError('UCRB canonical publication evidence missing') from error


def require_daily_acceptance(root, domain, config):
    if domain not in ('b120', 'yampa', 'ucrb'):
        raise ValueError('Unsupported new daily model domain')
    report = json.loads((root / config['daily_acceptance']).read_text())
    manifest = json.loads((root / f'nwm/static/domains/{domain}/manifest.json').read_text())
    require_nrt_publication(root, domain, config)
    if (report.get('status') != 'complete' or not report.get('test')
            or report.get('domain') != domain or report.get('rst_typ') != 1
            or report.get('daily_checkpoint_continuation') != 'passed'
            or report.get('mask_sha256') != manifest['mask_sha256']):
        raise ValueError('Domain-specific daily replay acceptance/promotion missing or changed')


def catchup_accepted(root, domain, gate):
    """Fail closed until the explicitly selected replay finishes publication.

    This is historical activation evidence, not a freshness audit of files that
    subsequent daily runs are expected to replace.
    """
    try:
        path = (root / gate['report']).resolve()
        if not path.is_relative_to((root / f'nwm/runs/{domain}/nrt/cycles').resolve()):
            return False
        report = json.loads(path.read_text())
        start, end = (datetime.fromisoformat(report[k]) for k in ('start', 'end'))
        if (report.get('status') != 'complete' or report.get('test') is not False
                or report.get('domain') != domain or report.get('rst_typ') != 1
                or report.get('job_id') != str(gate['job_id'])
                or start != datetime.fromisoformat(gate['start'])
                or end != datetime.fromisoformat(gate['end'])
                or report.get('checkpoint_count') != (end-start).days
                or not report.get('forcing_snapshot')):
            return False
        journal = path.parent / 'publication/transaction.json'
        if (root / report['journal']).resolve() != journal:
            return False
        transaction = json.loads(journal.read_text())
        return (transaction.get('status') == 'complete' and bool(transaction.get('files'))
                and len(transaction['files']) == report.get('published_files'))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def submit_if_enabled(root, dependencies, domain='cnrfc'):
    if domain not in ('cnrfc', 'b120', 'yampa', 'ucrb'):
        raise ValueError('Unsupported daily model domain')
    if domain == 'cnrfc':
        from hydro_ops.cnrfc_domain import migration_state
        if migration_state(root).get('blocked'):
            return {'status': 'disabled_cnrfc_grid_migration'}
    config = json.loads((root / f'config/{domain}_nrt_daily.json').read_text())
    if not config.get('enabled', False):
        return {'status': 'disabled_pending_acceptance'}
    gate = config.get('catchup_acceptance')
    if gate and not catchup_accepted(root, domain, gate):
        return {'status': 'disabled_pending_catchup_publication', 'report': gate['report']}
    if domain != 'cnrfc':
        require_daily_acceptance(root, domain, config)
    state = root / f'nwm/status/{domain}/nrt-daily'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        day = datetime.now(UTC).strftime('%Y%m%d')
        record_path = state / f'submission-{day}.json'
        if record_path.exists():
            return {'status': 'already_submitted', 'record': str(record_path)}
        queued = subprocess.check_output(['squeue', '--me', '-h', '-o', '%j'], text=True)
        if any(f'wrfh-{domain}' in name for name in queued.splitlines()):
            return {'status': 'deferred_model_job_active'}
        args = command(root, dependencies, config.get('rewind_days', 11), domain,
                       config.get('mpi_ranks', 64 if domain in ('cnrfc', 'ucrb') else 32))
        report = {'status': 'submitting', 'command': args}
        write_json(record_path, report)
        try:
            response = subprocess.check_output(args, cwd=root, text=True).strip().split(';')[0]
            if not response.isdecimal():
                raise ValueError('Unexpected sbatch response')
            report.update(status='submitted', job_id=response)
        except BaseException as error:
            report.update(status='submission_uncertain', error=str(error))
            raise
        finally:
            write_json(record_path, report)
        return report
