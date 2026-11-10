"""Explicit one-day replay promotion; dry-run by default, never enables cron."""
import argparse
import fcntl
import json
import os
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from backfill_cnrfc_forcing import process
from publish_prism_repair_wave import DOMAINS, PARENTS

from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.nrt_cycle import identity
from hydro_ops.forcing.prism_revision_queue import acknowledge, source_signature
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.rain_summary_publication import publish_summary
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temporal_summary import next_month, summarize, summary_path


def load(path):
    return json.loads(path.read_text())


def scratch_directory():
    path = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}/bounded-replay')
    path.mkdir(parents=True, exist_ok=True)
    return path


def bounded_plan(root, workspace, stream, day):
    """Require the exact accepted hourly date, complete downstream and no-op gates."""
    root, workspace = root.resolve(), workspace.resolve()
    if not workspace.is_relative_to(root/'forcing/work/isolated-revision-replay'):
        raise ValueError('Only isolated replay workspaces may be promoted')
    if stream not in ('nrt', 'retro'):
        raise ValueError('Unknown stream')
    acceptance, noop = load(workspace/'acceptance.json'), load(workspace/'noop.json')
    if (acceptance.get('status') != 'passed' or acceptance.get('stream') != stream
            or noop.get('status') != 'passed' or not noop.get('noop')):
        raise ValueError('Replay and unchanged-repeat acceptance required')
    stages = load(workspace/'stages.json')['stages']
    months = sorted({d.replace(day=1) for d in (day-timedelta(days=1), day)})
    monthly_stages = {'monthly'} if len(months) == 1 else {f'monthly/{m:%Y-%m}' for m in months}
    required = {'hourly', *monthly_stages, *(f'baseline/{day+timedelta(days=i)}' for i in (-1, 0, 1)),
                *(f'window/{day+timedelta(days=i)}' for i in (0, 1)),
                *(f'daily/{day+timedelta(days=i)}' for i in (-1, 0)),
                *(f'subsets/{d}' for d in DOMAINS)}
    if set(stages) != required:
        raise ValueError('Date and affected months not exactly covered by accepted replay')
    for record in stages.values():
        if record['status'] != 'passed':
            raise ValueError('Incomplete replay stage')
        for item in record['artifacts']:
            p = Path(item['path']).resolve()
            if not p.is_relative_to(workspace) or list(fingerprint(p)) != item['identity']:
                raise ValueError('Accepted replay artifact changed')
    context = load(workspace/'context.json')
    expected_sources = {f'{var}/{day+timedelta(days=i)}' for i in (0, 1)
                        for var in ('ppt', 'tmin', 'tmax')}
    if set(context['prism']) != expected_sources:
        raise ValueError('Exactly six pinned PRISM sources required')
    for p, pin in context['pins'].items():
        if list(fingerprint(p)) != pin:
            raise ValueError(f'Pinned context changed: {p}')
    for sig in context['prism'].values():
        if source_signature(Path(sig['path'])) != sig:
            raise ValueError('PRISM superseded; rebuild candidate')
    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    candidate = workspace/'outputs/conus'/stream/'hourly'/relative
    if [str(candidate)] != [a['path'] for a in stages['hourly']['artifacts']]:
        raise ValueError('Hourly candidate does not match requested date')
    daily = [day-timedelta(days=1), day]
    month = day.replace(day=1)
    targets = []
    for domain in ('conus', *DOMAINS):
        base = root/'forcing/outputs'/domain/stream
        targets.extend([base/'hourly'/relative,
                        *(summary_path(base/'daily', d) for d in daily),
                        *(summary_path(base/'monthly', m, monthly=True) for m in months)])
    for p in targets:
        if p.resolve() != p or not p.is_file():
            raise ValueError(f'Initial bounded promotion requires existing regular targets: {p}')
    return {'stream': stream, 'day': str(day), 'candidate': str(candidate),
            **({'months': list(map(str, months))} if len(months) > 1 else {}),
            'targets': {str(p): list(fingerprint(p)) for p in targets},
            'daily': list(map(str, daily)), 'month': str(month),
            'source_signatures': {k: v['signature'] for k, v in context['prism'].items()},
            'scope': ('one existing calendar day; two daily summaries; one month; all four subdomains'
                      if len(months) == 1 else
                      'one existing calendar day; two daily summaries; two months; all four subdomains')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--stream', choices=('nrt', 'retro'), required=True)
    parser.add_argument('--day', type=date.fromisoformat, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    work = args.workspace.resolve()
    plan = bounded_plan(root, work, args.stream, args.day)
    if not args.publish:
        print(json.dumps({'status': 'planned', 'production_modified': False, **plan}, indent=2))
        return
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Publication and full readback must run under SLURM')
    state = work/'publication'
    state.mkdir(exist_ok=True)
    scratch = scratch_directory()
    stream, day = args.stream, args.day
    base = root/'forcing/outputs/conus'/stream
    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    target = base/'hourly'/relative
    queue_dir = root/'forcing/status/prism-revisions'
    report = {'status': 'running', 'day': str(day), 'stream': stream,
              'job': os.environ['SLURM_JOB_ID'], 'originals_retained': True}
    with ExitStack() as stack:
        locks = [root/'forcing/work/update_nwm_forcing.lock',
                 root/'forcing/status/nrt-gfs/cycle.lock',
                 root/'forcing/outputs/conus/.rain-repair.lock']
        months = [date.fromisoformat(m) for m in plan.get('months', [plan['month']])]
        locks += [root/f'forcing/outputs/conus/.rain-repair-{m:%Y%m}.lock' for m in months]
        locks += [root/f'forcing/outputs/{d}/{name}' for d in DOMAINS
                  for name in ('.subset.lock', '.subset-pilot.lock')]
        for p in locks:
            p.parent.mkdir(parents=True, exist_ok=True)
            lock = stack.enter_context(p.open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        bounded_plan(root, work, stream, day)
        journal = state/'plan.json'
        if journal.exists():
            old = load(journal)
            if any(old[k] != plan[k] for k in plan if k != 'targets'):
                raise ValueError('Publication plan changed')
            plan = old
        else:
            atomic_json(journal, plan)
        completed = state/'acceptance.json'
        if completed.exists() and load(completed).get('status') == 'passed':
            previous = load(completed)
            for p, pin in previous['published_artifacts'].items():
                if list(fingerprint(p)) != pin:
                    raise ValueError('Previously published artifact changed; replan required')
            print(json.dumps({'status': 'unchanged', 'publication': str(completed)}))
            return
        progress_path = state/'downstream-progress.json'
        progress = load(progress_path) if progress_path.exists() else {}
        for p, pin in plan['targets'].items():
            if Path(p) != target and list(fingerprint(p)) != progress.get(p, pin):
                raise ValueError('Downstream destination changed outside recorded progress; inspect before retry')
        def record_progress(path):
            progress[str(path)] = list(fingerprint(path))
            atomic_json(progress_path, progress)
        # Snapshot every affected product and sidecar before the first replacement.
        for p, pin in plan['targets'].items():
            p = Path(p)
            backup = state/'originals'/p.relative_to(root/'forcing/outputs')
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                if list(fingerprint(p)) != pin:
                    raise ValueError('Production changed since planning')
                os.link(p, backup)
                for suffix in ('.manifest.json', '.nrt-receipt.json', '.subset.json'):
                    sidecar = p.with_name(p.name+suffix)
                    if sidecar.exists():
                        os.link(sidecar, backup.with_name(backup.name+suffix))
            if list(fingerprint(backup)) != pin:
                raise ValueError('Original snapshot changed')
        try:
            candidate = Path(plan['candidate'])
            context = load(work/'context.json')
            inputs = {p: pin for p, pin in context['pins'].items()}
            proof = {'status': 'passed', 'replay': str(work/'acceptance.json'),
                     'source_signatures': plan['source_signatures'], 'sha256': digest(candidate)}
            publish_summary(candidate, target, state/'hourly.json', inputs, proof)
            receipt = {'status': 'passed', 'day': str(day), 'forcing_stream': stream,
                       'complete_day': True, 'latest_model_ready_hour': f'{day}T23:00:00+00:00',
                       'prism_constrained': True, 'published_identity': identity(target),
                       'sha256': proof['sha256'], 'verification': 'accepted_coupled_replay_static_envelope',
                       'replay_provenance': proof, 'derived_products_pending': True,
                       'as_of': datetime.now(UTC).isoformat(), 'verified': True,
                       'daily_file': str(target)}
            metadata = target.with_name(target.name+'.manifest.json')
            atomic_json(metadata, receipt)
            if stream == 'nrt':
                atomic_json(target.with_name(target.name+'.nrt-receipt.json'), receipt)
            reducers, names, units = load_forcing_reducers(root/'config/forcing_daily_reducers.toml')
            daily_paths = []
            for stamp in plan['daily']:
                d = date.fromisoformat(stamp)
                out = summary_path(base/'daily', d)
                summarize(base/'hourly', out, d, d+timedelta(days=1), reducers, names, units,
                          domain='conus', stream=stream, skip_existing=True, replace_stale=True)
                record_progress(out)
                daily_paths.append(out)
            monthly_paths = []
            for month in months:
                monthly = summary_path(base/'monthly', month, monthly=True)
                summarize(base/'daily', monthly, month, next_month(month), reducers, names, units,
                          from_daily=True, domain='conus', stream=stream, skip_existing=True, replace_stale=True)
                record_progress(monthly)
                monthly_paths.append(monthly)
            for domain in DOMAINS:
                parent = root/'forcing/outputs'/PARENTS[domain]/stream
                child = root/'forcing/outputs'/domain/stream
                mask = root/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
                for p in (target, *daily_paths, *monthly_paths):
                    rel = p.relative_to(base)
                    source, output = parent/rel, child/rel
                    result = process((source, output, mask, digest(mask), scratch, list(fingerprint(source))))
                    if result['status'] not in ('published', 'skipped'):
                        raise ValueError(f'Subset failed: {result}')
                    record_progress(output)
            # Source freshness is checked again before acknowledgement.
            bounded_plan(root, work, stream, day)
            receipt['derived_products_pending'] = False
            atomic_json(metadata, receipt)
            if stream == 'nrt':
                atomic_json(target.with_name(target.name+'.nrt-receipt.json'), receipt)
            queue_dir.mkdir(parents=True, exist_ok=True)
            with (queue_dir/'queue.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                path = queue_dir/'queue.json'
                queue = load(path) if path.exists() else {}
                key = f'{stream}/{day}'
                pending = queue.get('pending', {}).get(key)
                if pending:
                    sigs = pending['sources']
                    if not sigs or any(plan['source_signatures'].get(k) != v for k, v in sigs.items()):
                        raise ValueError('Superseded queue event; retain pending work')
                    if not acknowledge(queue, key, sigs, {'status': 'passed', 'hourly': True,
                                                         'summaries': True, 'subsets': True}):
                        raise ValueError('Acknowledgement rejected')
                    atomic_json(path, queue)
                report['queue_acknowledged'] = bool(pending)
            published = [Path(p) for p in plan['targets']]
            for p in list(published):
                published.extend(p.with_name(p.name+s) for s in ('.manifest.json', '.nrt-receipt.json', '.subset.json')
                                 if p.with_name(p.name+s).exists())
            report['published_artifacts'] = {str(p): list(fingerprint(p)) for p in published}
            report['status'] = 'passed'
        except BaseException as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            atomic_json(state/'acceptance.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
