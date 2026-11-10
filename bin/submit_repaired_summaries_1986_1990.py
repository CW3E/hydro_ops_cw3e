#!/usr/bin/env python3
"""Submit audited CONUS summary replacements and dependent four-domain subsets."""
import argparse
import fcntl
import json
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    wave = root/'forcing/work/prism-repair-wave3-198602-199012'
    audit = json.loads((wave/'acceptance.json').read_text())
    if audit.get('status') != 'passed' or audit.get('candidate_files') != 1795:
        raise ValueError('Repair-wave audit has not passed')
    report = {'status': 'planned', 'jobs': [], 'scope': '1986-1990 repaired summary replacement',
              'deferred_daily': ['1990-12-31'], 'deferred_monthly': ['1990-12'],
              'reason': 'Needs repaired 1991-01-01 00 UTC boundary',
              'allocated_cpus_per_job': 16}
    commands = []
    for year in range(1986, 1991):
        end = f'{year}-12-30' if year == 1990 else f'{year}-12-31'
        command = ['sbatch', '--parsable',
                   f'--job-name=retro-repaired-conus-summaries-{year}',
                   'slurm/refresh_repaired_summaries.sh', '--start', f'{year}-01-01',
                   '--end', end, '--complete-months-only']
        commands.append((year, end, command))
    if not args.submit:
        print(json.dumps(report | {'commands': commands}, indent=2))
        return
    directory = root/'forcing/work/repaired-summary-submissions'
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory/'1986-1990.json'
    with (directory/'1986-1990.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if manifest.exists():
            raise FileExistsError(f'Inspect prior submission before retrying: {manifest}')
        report['status'] = 'submitting'
        atomic_json(manifest, report)

        def submit(command, year, stage):
            row = {'year': year, 'stage': stage, 'command': command, 'status': 'pending_submission'}
            report['jobs'].append(row)
            atomic_json(manifest, report)
            response = subprocess.check_output(command, cwd=root, text=True).strip()
            job = response.split(';')[0]
            if not job.isdigit():
                raise ValueError(f'Uncertain submission response: {response}')
            row.update(status='submitted', job_id=job)
            atomic_json(manifest, report)
            return job

        for year, end, command in commands:
            job = submit(command, year, 'conus_summaries')
            submit(['sbatch', '--parsable', f'--dependency=afterok:{job}',
                    f'--job-name=retro-repaired-summary-subsets-four-domains-{year}',
                    'slurm/subset_repaired_summaries.sh', '--year', str(year), '--end', end],
                   year, 'subsets')
        report['status'] = 'submitted'
        atomic_json(manifest, report)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
