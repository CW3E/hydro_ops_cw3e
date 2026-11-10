"""Real-data staged replay with persistent failure/retry; never writes production."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from backfill_cnrfc_forcing import process
from benchmark_static_mask_chunks import chunk_mask, verify
from publish_prism_repair_wave import DOMAINS, PARENTS

from hydro_ops.forcing import nrt_cycle
from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.prism_replay_execution import run_stage
from hydro_ops.forcing.prism_revision_queue import acknowledge, enqueue, source_signature
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.streams import validate_stream_output_root
from hydro_ops.forcing.temporal_summary import next_month, summarize, summary_path


def replay_paths(root, job, stream, resume_job=None):
    """Reuse only an existing isolated checkpoint; scratch remains job-local."""
    base = (root/'forcing/work/isolated-revision-replay').resolve()
    workspace_id = resume_job or job
    if not workspace_id.isascii() or not workspace_id.isdecimal():
        raise ValueError('Workspace job ID must be numeric')
    work = (base/workspace_id).resolve()
    if work.parent != base:
        raise ValueError('Workspace escapes isolated replay root')
    if resume_job and not all((work/name).is_file() for name in (
            'context.json', 'stages.json', 'queue.json', 'native-pins.json')):
        raise ValueError('Resume requires an existing isolated checkpoint')
    windows = validate_stream_output_root(work/'windows'/stream/'hourly', stream)
    calendar = validate_stream_output_root(work/'calendar'/stream/'hourly', stream)
    return work, windows, calendar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('daily', 'monthly'), required=True)
    parser.add_argument('--interrupt-after-hourly', action='store_true')
    parser.add_argument('--resume-job', help='Reuse the isolated workspace from this earlier job ID')
    parser.add_argument('--workspace-id', help='Numeric isolated workspace ID for a bounded multi-day batch')
    parser.add_argument('--day', type=date.fromisoformat, help='Explicit queued date for bounded commissioning')
    args = parser.parse_args()
    if args.workspace_id and args.resume_job:
        parser.error('Choose workspace-id or resume-job, not both')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    day = date(2026, 4, 12) if args.case == 'daily' else date(2026, 3, 12)
    default_day = day
    day = args.day or day
    months = sorted({d.replace(day=1) for d in (day-timedelta(days=1), day)})
    stream = 'nrt' if args.case == 'daily' else 'retro'
    job = os.environ['SLURM_JOB_ID']
    work, windows, calendar = replay_paths(root, args.workspace_id or job, stream, args.resume_job)
    work.mkdir(parents=True, exist_ok=True)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/revision-replay')
    scratch.mkdir(parents=True, exist_ok=True)
    outputs = work/'outputs'
    parent = outputs/f'conus/{stream}'
    context_path = work/'context.json'
    reducers, names, units = load_forcing_reducers(root/'config/forcing_daily_reducers.toml')
    envelope = root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
    key = f'{stream}/{day}'
    if not context_path.exists():
        context = {'pins': {}, 'prism': {}, 'case': args.case, 'day': str(day)}
        for offset in (0, 1):
            d = day+timedelta(days=offset)
            for var in ('ppt', 'tmin', 'tmax'):
                rel = Path(var)/d.strftime(f'%Y/%m/prism_{var}_us_25m_%Y%m%d.nc')
                source = root/'forcing/inputs/oregon_state/prism/an/4km/daily'/rel
                sig = source_signature(source)
                target = work/'prism'/rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if source_signature(source) != sig or digest(source) != digest(target):
                    raise ValueError('PRISM changed during isolated refresh snapshot')
                context['prism'][f'{var}/{d}'] = sig
                context['pins'][str(target)] = list(fingerprint(target))
        state = enqueue({}, context['prism'], lambda s, d: s == stream and d == day)
        atomic_json(work/'queue.json', state)
        # Pin read-only boundary hours and unchanged monthly context before work.
        for offset in (-1, 1):
            d = day+timedelta(days=offset)
            rel = d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            source = root/f'forcing/outputs/conus/{stream}/hourly'/rel
            target = parent/'hourly'/rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
            context['pins'][str(source)] = list(fingerprint(source))
        start, stop = months[0], next_month(months[-1])
        for offset in range((stop-start).days):
            d = start+timedelta(days=offset)
            if d in (day-timedelta(days=1), day):
                continue
            source = summary_path(root/f'forcing/outputs/conus/{stream}/daily', d)
            target = summary_path(parent/'daily', d)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
            context['pins'][str(source)] = list(fingerprint(source))
        for asset in [envelope, root/'config/forcing_daily_reducers.toml',
                      *(root/f'nwm/static/domains/{d}/masks/{d}_masks.nc' for d in DOMAINS)]:
            context['pins'][str(asset)] = list(fingerprint(asset))
        atomic_json(context_path, context)
    context = json.loads(context_path.read_text())
    def check_context():
        if context['case'] != args.case or context.get('day', str(default_day)) != str(day):
            raise ValueError('Case changed')
        for path, pin in context['pins'].items():
            if list(fingerprint(Path(path))) != pin:
                raise ValueError(f'Pinned input changed: {path}')
        for sig in context['prism'].values():
            if source_signature(Path(sig['path'])) != sig:
                raise ValueError('PRISM superseded; do not acknowledge')
    check_context()
    signature = nrt_cycle.fingerprint(context)
    executed, reused = [], []
    def stage(name, action):
        check_context()
        result = run_stage(work, name, signature, action)
        (reused if result['reused'] else executed).append(name)
        print(json.dumps({'stage': name, 'reused': result['reused'], 'job': job}), flush=True)
        return result
    def passed(paths, **acceptance):
        return {'status': 'passed', 'artifacts': paths, 'acceptance': acceptance}
    env = {**os.environ, 'HYDRO_OPS_PRISM_STRATEGY': 'support-aware-v2',
           'HYDRO_OPS_ARCHIVE_CHUNKS': '1', 'HYDRO_OPS_ARCHIVE_PRESERVE_SOURCE_CHUNKS': '0'}
    def run(*command):
        subprocess.run([sys.executable, *map(str, command)], check=True, env=env)
    engine = nrt_cycle.RecentNrt(root, scratch, datetime.now(UTC),
                               baseline_root=work/'baseline', output_root=work/'unused-nrt')
    # Each invocation checks native sources even when a stage checkpoint exists.
    native_path = work/'native-pins.json'
    def native_snapshot():
        snapshot = {}
        for offset in (-1, 0, 1):
            d = day+timedelta(days=offset)
            selections, inputs = nrt_cycle.baseline_plan(d, engine.layout, engine.config)
            if {s.product for s in selections} != {'nldas2'}:
                raise ValueError('Expected NLDAS: test must not acquire live fallback data')
            snapshot[str(d)] = nrt_cycle.fingerprint(inputs)
        snapshot['assets'] = [nrt_cycle.identity(Path(a['path'])) for a in engine.assets]
        return snapshot
    native = native_snapshot()
    if native_path.exists() and json.loads(native_path.read_text()) != native:
        raise ValueError('Native baseline sources changed; new workspace required')
    if not native_path.exists():
        atomic_json(native_path, native)
    for offset in (-1, 0, 1):
        d = day+timedelta(days=offset)
        def baseline(d=d):
            target, receipt = engine.baseline_day(d)
            if receipt['status'] != 'passed' or not receipt['complete_day']:
                raise ValueError('Baseline not accepted')
            return passed([target, target.with_name(target.name+'.nrt-receipt.json')])
        stage(f'baseline/{d}', baseline)
    for offset in (0, 1):
        d = day+timedelta(days=offset)
        def window(d=d):
            run('bin/produce_prism_constrained_daily.py', '--day', d, '--complete-root', work/'baseline',
                '--output-root', windows, '--stream', stream, '--revision', 'stable',
                '--prism-root', work/'prism', '--work-directory', scratch,
                '--archive-access', 'direct', '--allow-legacy-12utc-output')
            proof = windows/d.strftime('%Y/%m/%Y%m%d.prism_precipitation_diagnostics.json')
            record = json.loads(proof.read_text())
            if record['status'] != 'published_candidate' or record['fit_iteration_budget'] != 200:
                raise ValueError('Window gate failed')
            return passed([windows/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1'), proof])
        stage(f'window/{d}', window)
    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    def hourly():
        run('bin/materialize_calendar_forcing.py', '--input-root', windows, '--output-root', calendar,
            '--start', day, '--days', 1, '--stream', stream, '--require-accepted-prism-windows',
            '--hierarchical', '--work-directory', scratch)
        target = parent/'hourly'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        chunk_mask(calendar/relative, envelope, target)
        verify(calendar/relative, envelope, target)
        return passed([target])
    stage('hourly', hourly)
    if args.interrupt_after_hourly:
        state = json.loads((work/'queue.json').read_text())
        if key not in state['pending']:
            raise ValueError('Premature acknowledgement')
        atomic_json(work/'interruption.json', {'status': 'expected_interruption', 'queue_pending': True})
        return 75
    summaries = [day-timedelta(days=1), day]
    for d in summaries:
        def daily(d=d):
            target = summary_path(parent/'daily', d)
            summarize(parent/'hourly', target, d, d+timedelta(days=1), reducers, names, units,
                      domain='conus', stream=stream, skip_existing=True)
            return passed([target])
        stage(f'daily/{d}', daily)
    for start in months:
        def monthly(start=start):
            target = summary_path(parent/'monthly', start, monthly=True)
            summarize(parent/'daily', target, start, next_month(start), reducers, names, units,
                      from_daily=True, domain='conus', stream=stream, skip_existing=True)
            return passed([target])
        stage('monthly' if len(months) == 1 else f'monthly/{start:%Y-%m}', monthly)
    for domain in DOMAINS:
        def subset(domain=domain):
            source_root = outputs/f'{PARENTS[domain]}/{stream}'
            destination = outputs/f'{domain}/{stream}'
            mask = root/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
            sources = [source_root/'hourly'/relative,
                       *(summary_path(source_root/'daily', d) for d in summaries),
                       *(summary_path(source_root/'monthly', m, monthly=True) for m in months)]
            results = [process((p, destination/p.relative_to(source_root), mask, digest(mask), scratch,
                                list(fingerprint(p)))) for p in sources]
            if any(r['status'] not in ('published', 'skipped') for r in results):
                raise ValueError(f'Subsets failed: {results}')
            return passed([Path(r['output']) for r in results])
        stage(f'subsets/{domain}', subset)
    check_context()
    if native_snapshot() != native:
        raise ValueError('Native sources changed during replay; do not acknowledge')
    state = json.loads((work/'queue.json').read_text())
    signatures = {k: v['signature'] for k, v in context['prism'].items()}
    was_pending = key in state['pending']
    if was_pending:
        if not acknowledge(state, key, signatures, {'status': 'passed', 'hourly': True,
                                                  'summaries': True, 'subsets': True}):
            raise ValueError('Exact-source acknowledgement failed')
        atomic_json(work/'queue.json', state)
    elif executed:
        raise ValueError('Acknowledged replay unexpectedly reran work')
    result = {'status': 'passed', 'job': job, 'workspace': str(work),
              'case': args.case, 'stream': stream, 'executed': executed,
              'reused': reused, 'queue_pending': len(state['pending']), 'production_modified': False,
              'live_queue_modified': False, 'source_refresh': 'isolated snapshot of downloaded sources, no live acquisition',
              'full_live_cron_test': False, 'noop': not was_pending and not executed}
    atomic_json(work/('noop.json' if result['noop'] else 'acceptance.json'), result)
    print(json.dumps(result), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
