"""Refresh boundary-affected summaries, then their four audited subsets."""
import argparse
import fcntl
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

from backfill_cnrfc_forcing import discover, process
from publish_prism_repair_wave import DOMAINS, PARENTS

from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--period', choices=('198012', '202604'), required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    output = root/'forcing/outputs/conus/retro'
    first, last = {'198012': ('1980-12-01', '1980-12-31'),
                   '202604': ('2026-04-01', '2026-04-17')}[args.period]
    state = root/'forcing/work/rain-repair-endpoints'/f'summaries-{args.period}-{job}.json'
    report = {'status': 'running', 'start': first, 'end': last, 'subsets': {}}
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/summary-subsets')
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([sys.executable, 'bin/backfill_forcing_summaries.py',
                        '--input-root', str(output/'hourly'), '--output-root', str(output),
                        '--stream', 'retro', '--replace-stale-retro', '--complete-months-only',
                        '--start', first, '--end', last, '--workers', '4'], check=True)
        with ExitStack() as stack:
            for domain in DOMAINS:
                lock = stack.enter_context((root/f'forcing/outputs/{domain}/.subset.lock').open('a'))
                fcntl.flock(lock, fcntl.LOCK_EX)
            for domain in DOMAINS:
                parent = root/f'forcing/outputs/{PARENTS[domain]}/retro'
                target = root/f'forcing/outputs/{domain}/retro'
                mask = root/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
                paths = [p for p in discover(parent.parent, ('retro',), ('daily', 'monthly'))
                         if p.name.startswith(args.period)
                         and (len(p.name.split('.')[0]) == 6 or p.name[:8] <= last.replace('-', ''))
                         and (args.period != '202604' or p.name.endswith('.daily'))]
                rows = []
                for p in paths:
                    result = process((p, target/p.relative_to(parent), mask, digest(mask),
                                      scratch, list(fingerprint(p))))
                    if result['status'] not in ('published', 'skipped'):
                        raise ValueError(str(result))
                    rows.append(result)
                report['subsets'][domain] = rows
                atomic_json(state, report)
        report['status'] = 'passed'
        if args.period == '202604':
            report['monthly_deferred'] = 'April is incomplete; no partial monthly product published'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(state, report)


if __name__ == '__main__':
    main()
