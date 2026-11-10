"""Submit independent B120 continuation and UCRB final-interval retry gates."""
import argparse
import os
import subprocess
from pathlib import Path

from continue_yampa_retro import continuation

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', required=True, choices=('b120', 'ucrb'))
    args = parser.parse_args()
    domain = args.domain
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    first, label = continuation(domain)
    ledger = root/'nwm/status/repaired_rain_retro_v1'/label/'submission.json'
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open('x') as handle:
        handle.write('[]\n')
    env = dict(os.environ)
    env.pop('SBATCH_RESERVATION', None)
    rows = []

    def submit(command, **details):
        job = subprocess.check_output(['sbatch', '--parsable', *command], cwd=root,
                                      env=env, text=True).strip().split(';')[0]
        rows.append({'job_id': job, 'command': command, **details})
        atomic_json(ledger, rows)
        print(job, details, flush=True)
        return job

    previous = submit(['--partition=shared-128', '--ntasks=1', '--cpus-per-task=4',
                       '--time=02:00:00', f'--job-name={domain}-retro-forcing-gate-{first}-20260417',
                       f'--output=nwm/logs/{domain}-continuation-preflight-%j.out',
                       ('--wrap=source bin/project_environment.sh; "$HYDRO_OPS_PYTHON" '
                        f'bin/continue_yampa_retro.py --preflight --domain {domain}')], stage='preflight')
    ranks = 64 if domain == 'ucrb' else 32
    for year in range(first, 2027):
        interval = '20201231-20210101' if year == 2020 else str(year)
        previous = submit([f'--dependency=afterok:{previous}', f'--ntasks={ranks}',
                           f'--job-name=wrfh-{domain}-repaired-retro-{interval}-{ranks}r-monthly-rst',
                           f'--output=nwm/logs/{domain}-retro-continuation-%j.out',
                           'slurm/continue_yampa_retro.sh', '--domain', domain, '--year', str(year)],
                          year=year, ranks=ranks)


if __name__ == '__main__':
    main()
