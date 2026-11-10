"""Submit Yampa forcing gate, one-day 2020 bridge, and 2021–April 2026 runs."""
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    ledger = root/'nwm/status/repaired_rain_retro_v1/yampa-extension-20201231-20260417/submission.json'
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
                       '--time=01:00:00', '--job-name=yampa-retro-forcing-gate-20201231-20260417',
                       '--output=nwm/logs/yampa-continuation-preflight-%j.out',
                       '--wrap=source bin/project_environment.sh; "$HYDRO_OPS_PYTHON" bin/continue_yampa_retro.py --preflight'],
                      stage='preflight')
    for year in range(2020, 2027):
        label = '20201231-20210101' if year == 2020 else str(year)
        previous = submit([f'--dependency=afterok:{previous}',
                           f'--job-name=wrfh-yampa-repaired-retro-{label}-32r-monthly-rst',
                           'slurm/continue_yampa_retro.sh', '--year', str(year)], year=year, ranks=32)


if __name__ == '__main__':
    main()
