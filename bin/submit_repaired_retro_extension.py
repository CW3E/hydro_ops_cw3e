"""Submit the gated B120/Yampa 2001–2020 continuation without touching other chains."""
import argparse
import json
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ucrb', action='store_true', help='UCRB 2001 through April 17, 2026 00 UTC')
    parser.add_argument('--cnrfc', action='store_true', help='CNRFC 2001 through April 17, 2026 00 UTC')
    parser.add_argument('--after', help='Successful job dependency for the provenance preflight')
    args = parser.parse_args()
    if args.ucrb and args.cnrfc:
        parser.error('Choose only one domain')
    if args.after and not args.after.isdecimal():
        parser.error('--after must be a numeric job ID')
    extended = 'cnrfc' if args.cnrfc else 'ucrb' if args.ucrb else None
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    label = f'{extended}-extension-2001-20260417' if extended else 'extension-2001-2020'
    ledger = root/'nwm/status/repaired_rain_retro_v1'/label/'submission.json'
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open('x') as handle:
        json.dump([], handle)
    env = dict(os.environ)
    env.pop('SBATCH_RESERVATION', None)
    rows = []

    def submit(command, **details):
        job = subprocess.check_output(['sbatch', '--parsable', *command],
                                      cwd=root, env=env, text=True).strip().split(';')[0]
        rows.append({'job_id': job, 'command': command, **details})
        atomic_json(ledger, rows)
        print(json.dumps(rows[-1]), flush=True)
        return job

    preflight = submit(['--partition=shared-128', '--ntasks=1', '--cpus-per-task=4',
                        *([f'--dependency=afterok:{args.after}'] if args.after else []),
                        '--time=02:00:00', f'--job-name=wrfh-{label}-forcing-provenance-gate',
                        '--output=nwm/logs/repaired-retro-extension-preflight-%j.out',
                        '--wrap=source bin/project_environment.sh; "$HYDRO_OPS_PYTHON" bin/extend_repaired_retro.py --preflight'
                        + (f' --domain {extended}' if extended else '')],
                       stage='preflight')
    for domain in ((extended,) if extended else ('b120', 'yampa')):
        ranks = 64 if extended else 32
        last_year = 2026 if extended else 2020
        end = '2026-04-17 00:00:00' if extended else '2020-12-31 00:00:00'
        previous = preflight
        for year in range(2001, last_year+1):
            previous = submit([f'--dependency=afterok:{previous}',
                               f'--ntasks={ranks}',
                               f'--job-name=wrfh-{domain}-repaired-retro-{year}-{ranks}r-monthly-rst',
                               'slurm/extend_repaired_retro.sh', '--domain', domain, '--year', str(year)],
                              domain=domain, year=year, ranks=ranks,
                              end=end if year == last_year else f'{year+1}-01-01 00:00:00')


if __name__ == '__main__':
    main()
