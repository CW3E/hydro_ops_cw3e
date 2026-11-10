#!/usr/bin/env python3
"""Publish accepted 1981 candidates and rebuild four hourly domain subsets.

Cron and model consumers must remain paused until summaries and model readiness
are separately accepted. This command deliberately does not remove originals.
"""
import argparse
import calendar
import fcntl
import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from backfill_cnrfc_forcing import process
from netCDF4 import Dataset

from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json, publish, sandbox

DOMAINS = ('cnrfc', 'b120', 'ucrb', 'yampa')
PARENTS = {'cnrfc': 'conus', 'b120': 'cnrfc', 'ucrb': 'conus', 'yampa': 'ucrb'}
WAVES = {
    'wave1': ('prism-repair-wave1-198101-198108', '5092482', date(1981, 1, 1),
              date(1981, 8, 31), 243),
    'wave2': ('prism-repair-wave2-198109-198601', '5093030', date(1981, 9, 1),
              date(1986, 1, 31), 1614),
}


@lru_cache(maxsize=4)
def envelope_asset(root):
    path = root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
    with Dataset(path) as ds:
        if ds.policy != 'nldas2_seven_met_static_envelope_v4':
            raise ValueError('Unexpected envelope policy')
        return {'policy': ds.policy, 'mask': str(path), 'mask_sha256': ds.keep_sha256,
                'mask_file_sha256': digest(path)}


def envelope_metadata(root, old, candidate):
    """Do not require historical metadata, but require new masking evidence."""
    if isinstance(old.get('static_envelope'), dict):
        envelope = dict(old['static_envelope'])
    else:
        proof = candidate.get('envelope_assembly', {})
        if (proof.get('retained_window_rain_exact') is not True
                or proof.get('original_windows_modified') is not False
                or not proof.get('accepted_window_identities')
                or not candidate.get('unchanged_chunks_verified')):
            raise ValueError('Missing historical envelope and verified assembly evidence')
        envelope = dict(envelope_asset(root))
        envelope['metadata_origin'] = 'verified_envelope_reassembly'
    for key in ('published_utc', 'staged_identity', 'audit_scope'):
        envelope.pop(key, None)
    envelope.update(valid_outside_mask=0, verified_fields=['RAINRATE'],
                    nonrain_verification='compressed chunks unchanged from original',
                    audit_scope='final rain readback and envelope validation; nonrain raw-chunk equality')
    return envelope


def month_index(day, first):
    return (day.year-first.year)*12 + day.month-first.month


def month_range(first, index, last=None):
    number = first.year*12 + first.month-1 + index
    year, month = divmod(number, 12)
    month += 1
    end = date(year, month, calendar.monthrange(year, month)[1])
    return date(year, month, 1), min(end, last) if last is not None else end


def acquire_locks(stack, root, start, end, *, blocking=False):
    wait_flag = 0 if blocking else fcntl.LOCK_NB
    for path in [root / 'forcing/outputs/conus/.rain-repair.lock', *[
            root / f'forcing/outputs/{domain}/.subset-pilot.lock' for domain in DOMAINS], *[
            root / f'forcing/outputs/{domain}/.subset.lock' for domain in DOMAINS]]:
        lock = stack.enter_context(path.open('a'))
        fcntl.flock(lock, fcntl.LOCK_SH | wait_flag)
    for index in range(month_index(end, start)+1):
        month, _ = month_range(start, index)
        path = root / f'forcing/outputs/conus/.rain-repair-{month:%Y%m}.lock'
        lock = stack.enter_context(path.open('a'))
        fcntl.flock(lock, fcntl.LOCK_EX | wait_flag)


def publish_subsets(root, relative, scratch):
    """Refresh parents before children, retaining existing receipt provenance."""
    subsets = {}
    for domain in DOMAINS:
        parent = PARENTS[domain]
        source = root / f'forcing/outputs/{parent}/retro/hourly' / relative
        mask = root / f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
        child = root / f'forcing/outputs/{domain}/retro/hourly' / relative
        task = (source, child, mask, digest(mask), scratch, list(fingerprint(source)))
        result = process(task)
        if result['status'] not in ('published', 'skipped'):
            raise RuntimeError(f'{domain} subset failed: {result}')
        subsets[domain] = result
    return subsets


def publish_day(options):
    root, wave, output, scratch, array, first, day = options
    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    target = root / 'forcing/outputs/conus/retro/hourly' / relative
    task = month_index(day, first)
    audit = root / f'forcing/work/prism-rain-pipeline/{array}_{task}/acceptance.json'
    accepted = json.loads(audit.read_text())
    rows = [r for r in accepted['files'] if Path(r['path']).name == target.name]
    if accepted['status'] != 'passed' or len(rows) != 1:
        raise ValueError(f'Missing accepted candidate: {day}')
    candidate = rows[0]
    plan_path = output / f'{day}.plan.json'
    old_manifest_path = output / f'{day}.original-manifest.json'
    manifest_path = Path(str(target) + '.manifest.json')
    if not plan_path.exists():
        backup = wave / 'originals' / relative
        if fingerprint(target) != fingerprint(backup):
            raise ValueError(f'Original identity changed: {day}')
        original_manifest = json.loads(manifest_path.read_text())
        if not old_manifest_path.exists():
            atomic_json(old_manifest_path, original_manifest)
        elif json.loads(old_manifest_path.read_text()) != original_manifest:
            raise ValueError('Original manifest changed during plan preparation')
        atomic_json(plan_path, {
            'authorization': 'audited-rain-only-production-v1',
            'target': str(target), 'candidate': candidate['path'],
            'original_snapshot': str(backup), 'old_identity': list(fingerprint(target)),
            'old_sha256': digest(backup), 'new_sha256': candidate['sha256'],
            'audit': str(audit), 'audit_sha256': digest(audit),
            'receipt': str(output / f'{day}.transaction.json')})
    # Validate metadata requirements before any new permanent replacement.
    old = json.loads(old_manifest_path.read_text())
    envelope = envelope_metadata(root, old, candidate)
    result = publish(plan_path, root, production=True)
    identity = fingerprint(target)
    identity_dict = dict(zip(('inode', 'bytes', 'mtime_ns'), identity))
    evidence_path = output / f'{day}.publication.json'
    evidence = {'status': 'published', 'published_identity': identity_dict,
                'file_sha256': candidate['sha256'], 'candidate_audit': str(audit),
                'policy': 'support-aware-rain-only-v1',
                'nonrain_compressed_chunks_unchanged': True,
                'active_rain_coverage_verified': True, 'outside_envelope_missing': True}
    atomic_json(evidence_path, evidence)
    envelope.update(audit=str(evidence_path), published_identity=identity_dict,
                    file_sha256=candidate['sha256'], active_values_unchanged=False,
                    retained_values_unchanged=False)
    envelope.pop('published_utc', None)
    atomic_json(manifest_path, {
        'day': str(day), 'daily_file': str(target), 'verified': True,
        'verification': 'rain_readback_active_envelope_and_nonrain_raw_chunks',
        'compression': old.get('compression'), 'static_envelope': envelope,
        'rain_repair': evidence, 'previous_manifest': str(old_manifest_path),
        'derived_products_pending': True})
    # An hour-00 change also affects the previous interval-end day.
    stale = [str(day-timedelta(days=1)), str(day)]
    atomic_json(output / f'{day}.derived-pending.json', {
        'domains': ['conus', *DOMAINS], 'stream': 'retro',
        'daily_dates': stale, 'monthly_periods': sorted({d[:7] for d in stale}),
        'status': 'pending', 'reason': 'hourly precipitation replaced'})
    subsets = publish_subsets(root, relative, scratch)
    return {'day': str(day), 'publication': result, 'subsets': subsets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wave', choices=WAVES, default='wave1')
    parser.add_argument('--wave-dir', type=Path)
    parser.add_argument('--array-job')
    parser.add_argument('--workers', type=int, choices=(1,2,4), default=1)
    parser.add_argument('--start', type=date.fromisoformat)
    parser.add_argument('--end', type=date.fromisoformat)
    parser.add_argument('--array-month', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    name, array, first, last, count = WAVES[args.wave]
    wave = root / 'forcing/work' / name
    if args.wave_dir:
        wave = sandbox(args.wave_dir, root)
        plan = json.loads((wave/'plan.json').read_text())
        if plan['status'] != 'prepared' or not args.array_job or not args.array_job.isdigit():
            parser.error('Prepared custom wave and numeric --array-job required')
        array = args.array_job
        first = date.fromisoformat(plan['months'][0]['start'])
        last_start = date.fromisoformat(plan['months'][-1]['start'])
        last = last_start + timedelta(days=plan['months'][-1]['days']-1)
        count = sum(m['days'] for m in plan['months'])
        if first < date(1981,1,1):
            parser.error('Daily PRISM publication cannot touch monthly-tier years')
    elif args.array_job:
        parser.error('--array-job requires --wave-dir')
    if args.array_month:
        if args.start or args.end:
            parser.error('Array month cannot be combined with explicit dates')
        index = int(os.environ['SLURM_ARRAY_TASK_ID'])
        if not 0 <= index <= month_index(last, first):
            parser.error('Array task outside approved wave')
        args.start, args.end = month_range(first, index, last)
    if not args.start or not args.end or not first <= args.start <= args.end <= last:
        parser.error('Dates must lie within the approved wave')
    acceptance = json.loads((wave / 'acceptance.json').read_text())
    if (acceptance['status'] != 'passed' or acceptance['candidate_files'] != count
            or len(acceptance['boundaries']) != month_index(last, first)
            or not all(row['shared_window_exact'] for row in acceptance['boundaries'])):
        raise ValueError('Complete wave/boundary acceptance missing')
    if args.wave_dir and acceptance.get('array_job') != array:
        raise ValueError('Accepted wave belongs to another candidate array')
    if args.wave_dir and plan.get('previous_window') and not acceptance.get('preceding_boundary', {}).get('shared_window_exact'):
        raise ValueError('Preceding wave boundary not accepted')
    output = sandbox(wave / 'production', root)
    output.mkdir(exist_ok=True)
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}/publish")
    scratch.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'days': [], 'originals_retained': True,
              'summaries_pending': True, 'model_runs_authorized': False, 'workers': args.workers}
    report_path = output / f'job-{job}.json'
    with ExitStack() as stack:
        # Shared campaign locks exclude existing exclusive cron/backfill writers;
        # exclusive calendar-month locks serialize overlapping repair publishers.
        acquire_locks(stack, root, args.start, args.end)
        atomic_json(report_path, report)
        try:
            days = [args.start+timedelta(days=i) for i in range((args.end-args.start).days+1)]
            tasks = [(root, wave, output, scratch, array, first, day) for day in days]
            with ProcessPoolExecutor(args.workers, mp_context=multiprocessing.get_context('spawn')) as pool:
                for result in pool.map(publish_day, tasks):
                    report['days'].append(result)
                    atomic_json(report_path, report)
            report['status'] = 'passed'
        except BaseException as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            atomic_json(report_path, report)


if __name__ == '__main__':
    main()
