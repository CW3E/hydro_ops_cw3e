"""Isolated coupled PRISM window → calendar → envelope → summary → subset test."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from backfill_cnrfc_forcing import process
from benchmark_static_mask_chunks import chunk_mask, verify, verify_chunks
from netCDF4 import Dataset, num2date
from publish_prism_repair_wave import DOMAINS, PARENTS

from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.prism_replay import dependencies
from hydro_ops.forcing.prism_revision_queue import acknowledge, source_signature
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temporal_summary import next_month, summarize, summary_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('daily', 'monthly'), required=True)
    parser.add_argument('--full-month-nrt', action='store_true',
                        help='Test stable April revision propagation through a complete NRT month')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    day = date(2026, 10, 25) if args.case == 'daily' else date(2026, 4, 12)
    stream = 'nrt' if args.case == 'daily' else 'retro'
    if args.full_month_nrt:
        if args.case != 'monthly':
            parser.error('--full-month-nrt requires --case monthly')
        stream = 'nrt'
    revision = 'provisional' if args.case == 'daily' else 'stable'
    job = os.environ['SLURM_JOB_ID']
    work = root/f'forcing/work/operational-prism-revision/{job}'
    work.mkdir(parents=True, exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/prism-operations-test')
    scratch.mkdir(parents=True, exist_ok=False)
    baseline = root/'forcing/outputs/conus/baseline/hourly'
    windows = scratch/'windows'/stream
    calendar = scratch/'calendar'/stream
    out = work/'outputs'
    report = {'status': 'running', 'case': args.case, 'production_modified': False,
              'source_pins': {}, 'windows': [], 'files': [], 'subsets': {},
              'dependency_plan': dependencies(day)}
    started = time.monotonic()
    env = {**os.environ, 'HYDRO_OPS_PRISM_STRATEGY': 'support-aware-v2',
           'HYDRO_OPS_ARCHIVE_CHUNKS': '1', 'HYDRO_OPS_ARCHIVE_PRESERVE_SOURCE_CHUNKS': '0'}
    def run(*command):
        subprocess.run([sys.executable, *map(str, command)], check=True, env=env)
    try:
        for i in (-1, 0, 1, 2):
            p = baseline/(day+timedelta(days=i)).strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            report['source_pins'][str(p)] = list(fingerprint(p))
        for i in range(3):
            d = day+timedelta(days=i)
            for var in ('ppt', 'tmin', 'tmax'):
                relative = Path(var)/d.strftime(f'%Y/%m/prism_{var}_us_25m_%Y%m%d.nc')
                source = root/'forcing/inputs/oregon_state/prism/an/4km/daily'/relative
                stamp = source_signature(source)
                report.setdefault('prism_sources', {})[f'{var}/{d}'] = stamp
                frozen = work/'prism'/relative
                frozen.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, frozen)
                if source_signature(source) != stamp or digest(source) != digest(frozen):
                    raise ValueError('PRISM changed during freeze')
        for i in range(3):
            d = day+timedelta(days=i)
            run('bin/produce_prism_constrained_daily.py', '--day', d,
                '--complete-root', baseline, '--output-root', windows, '--stream', stream,
                '--revision', revision, '--prism-root', work/'prism', '--work-directory', scratch,
                '--archive-access', 'direct', '--allow-legacy-12utc-output')
            proof = json.loads((windows/d.strftime('%Y/%m/%Y%m%d.prism_precipitation_diagnostics.json')).read_text())
            if proof['status'] != 'published_candidate' or proof['fit_iteration_budget'] != 200:
                raise ValueError('Window audit/bounded-cost gate failed')
            report['windows'].append({'day': str(d), 'acceptance_class': proof['acceptance_class'],
                                      'elapsed_seconds': proof['elapsed_seconds']})
            atomic_json(work/'acceptance.json', report)
        run('bin/materialize_calendar_forcing.py', '--input-root', windows, '--output-root', calendar,
            '--start', day, '--days', 2, '--stream', stream, '--require-accepted-prism-windows',
            '--hierarchical', '--work-directory', scratch)
        envelope = root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
        for i in range(2):
            d = day+timedelta(days=i)
            relative = Path(d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1'))
            source = calendar/relative
            target = out/f'conus/{stream}/hourly'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            mask_report = chunk_mask(source, envelope, target)
            mask_report['chunks'] = verify_chunks(source, envelope, target)
            mask_report['full_verification_seconds'] = verify(source, envelope, target)
            with Dataset(target) as ds:
                times = num2date(ds['time'][:], ds['time'].units)
                if [(x.year, x.month, x.day, x.hour) for x in times] != [(d.year,d.month,d.day,h) for h in range(24)]:
                    raise ValueError('Not a 00–23 calendar day')
            report['files'].append({'path': str(target), 'envelope': mask_report})
        parent = out/f'conus/{stream}'
        reducers, names, units = load_forcing_reducers(root/'config/forcing_daily_reducers.toml')
        summary_days = [day]
        if args.full_month_nrt:
            # Two changed calendar files also affect the preceding interval-end
            # day. Boundary hours and unchanged daily records remain read-only.
            summary_days = [day+timedelta(days=i) for i in (-1, 0, 1)]
            for i in (-1, 2):
                relative = (day+timedelta(days=i)).strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                source = root/f'forcing/outputs/conus/{stream}/hourly'/relative
                report['source_pins'][str(source)] = list(fingerprint(source))
                target = parent/'hourly'/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(source)
        for d in summary_days:
            summarize(parent/'hourly', summary_path(parent/'daily', d), d, d+timedelta(days=1),
                      reducers, names, units, domain='conus', stream=stream)
        report['daily_summaries'] = [str(summary_path(parent/'daily', d)) for d in summary_days]
        monthly = None
        if args.full_month_nrt:
            start = day.replace(day=1)
            stop = next_month(start)
            for i in range((stop-start).days):
                d = start+timedelta(days=i)
                if d in summary_days:
                    continue
                source = summary_path(root/f'forcing/outputs/conus/{stream}/daily', d)
                report['source_pins'][str(source)] = list(fingerprint(source))
                target = summary_path(parent/'daily', d)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(source)
            monthly = summary_path(parent/'monthly', start, monthly=True)
            summarize(parent/'daily', monthly, start, stop, reducers, names, units,
                      from_daily=True, domain='conus', stream=stream)
            pin = fingerprint(monthly)
            retry = summarize(parent/'daily', monthly, start, stop, reducers, names, units,
                              from_daily=True, domain='conus', stream=stream, skip_existing=True)
            if retry['status'] != 'unchanged' or fingerprint(monthly) != pin:
                raise ValueError('Monthly no-op retry rewrote output')
            report['monthly_summary'] = str(monthly)
            report['monthly_noop_retry'] = retry
        for domain in DOMAINS:
            parent = out/f'{PARENTS[domain]}/{stream}'
            target = out/f'{domain}/{stream}'
            mask = root/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
            paths = [parent/'hourly'/(day+timedelta(days=i)).strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1') for i in range(2)]
            paths.extend(summary_path(parent/'daily', d) for d in summary_days)
            if monthly:
                paths.append(summary_path(parent/'monthly', day.replace(day=1), monthly=True))
            results = [process((p, target/p.relative_to(parent), mask, digest(mask), scratch,
                                list(fingerprint(p)))) for p in paths]
            if any(r['status'] != 'published' for r in results):
                raise ValueError(f'Subset failure: {results}')
            report['subsets'][domain] = results
            if args.full_month_nrt:
                pins = {r['output']: fingerprint(Path(r['output'])) for r in results}
                retries = [process((p, target/p.relative_to(parent), mask, digest(mask), scratch,
                                    list(fingerprint(p)))) for p in paths]
                if any(r['status'] != 'skipped' for r in retries) or any(
                        fingerprint(Path(p)) != pin for p, pin in pins.items()):
                    raise ValueError('Subset no-op retry rewrote output')
                report.setdefault('subset_noop_retries', {})[domain] = retries
        for p, pin in report['source_pins'].items():
            if list(fingerprint(Path(p))) != pin:
                raise ValueError('Baseline changed during test')
        for info in report['prism_sources'].values():
            if source_signature(Path(info['path'])) != info:
                raise ValueError('Live PRISM changed during test; cannot acknowledge')
        key = f'{stream}/{day}'
        signatures = {k: v['signature'] for k, v in report['prism_sources'].items()}
        state = {'pending': {key: {'sources': signatures}}}
        if acknowledge(state, key, signatures, {'status': 'passed', 'hourly': True, 'summaries': False, 'subsets': True}):
            raise ValueError('Incomplete downstream work acknowledged')
        if args.full_month_nrt:
            acceptance = {'status': 'passed', 'hourly': True, 'summaries': True, 'subsets': True}
            newer = dict(signatures, superseded='newer-release')
            state['pending'][key]['sources'] = newer
            if acknowledge(state, key, signatures, acceptance):
                raise ValueError('Superseded revision incorrectly acknowledged')
            state['pending'][key]['sources'] = signatures
            if not acknowledge(state, key, signatures, acceptance) or state['pending']:
                raise ValueError('Completed exact-source revision not acknowledged')
            report['isolated_acknowledgement_checks'] = 'passed'
        report.update(status='passed', elapsed_seconds=time.monotonic()-started,
                      live_queue_modified=False, monthly_full_cycle_test_pending=not args.full_month_nrt,
                      automatic_replay_executor_test_pending=True)
    except BaseException as error:
        report.update(status='failed', error=str(error), elapsed_seconds=time.monotonic()-started)
        raise
    finally:
        atomic_json(work/'acceptance.json', report)


if __name__ == '__main__':
    main()
