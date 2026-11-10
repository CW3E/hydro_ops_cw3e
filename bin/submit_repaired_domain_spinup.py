#!/usr/bin/env python3
"""Preview/submit four independent 1981–1985 corrected-forcing spin-up chains."""
import argparse
import fcntl
import json
import os
import subprocess
from pathlib import Path

from run_repaired_domain_spinup import CAMPAIGN, GATES

from hydro_ops.wrf_hydro.production import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    plan = {'campaign': CAMPAIGN, 'years': list(range(1981, 1986)),
            'ranks': {'cnrfc': 64, 'ucrb': 64, 'b120': 32, 'yampa': 32},
            'forcing_repairs_modified': False, 'jobs': [], 'status': 'planned'}
    for domain, job in GATES.items():
        gate = json.loads((root / f'nwm/outputs/{domain}/tests/repaired-spinup-{job}'
                          / 'acceptance.json').read_text())
        if gate.get('status') != 'passed' or gate.get('restart_equivalence') != 'passed':
            raise ValueError(f'Model test gate failed: {domain}')
    if not args.submit:
        print(json.dumps(plan, indent=2))
        return
    directory = root / 'nwm/status' / CAMPAIGN
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / 'submission.json'
    with (directory / 'submit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if manifest.exists():
            raise FileExistsError(f'Inspect existing submission; do not duplicate: {manifest}')
        plan['status'] = 'submitting'
        write_json(manifest, plan)
        for domain, ranks in plan['ranks'].items():
            previous = None
            for year in plan['years']:
                command = ['sbatch', '--parsable', f'--ntasks={ranks}',
                           f'--job-name=wrfh-{domain}-repaired-spinup-{year}-monthly-rst']
                if previous:
                    command.append(f'--dependency=afterok:{previous}')
                command += ['slurm/run_repaired_domain_spinup.sh', domain, str(year)]
                row = {'domain': domain, 'year': year, 'mpi_ranks': ranks,
                       'command': command, 'status': 'submission_pending'}
                plan['jobs'].append(row)
                write_json(manifest, plan)
                # An interrupted/uncertain submission is recorded and never blindly retried.
                result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=True)
                previous = result.stdout.strip().split(';')[0]
                if not previous.isdigit():
                    raise ValueError(f'Unrecognized sbatch response: {result.stdout}')
                row.update(status='submitted', job_id=previous)
                write_json(manifest, plan)
        plan['status'] = 'submitted'
        write_json(manifest, plan)
        print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    main()
