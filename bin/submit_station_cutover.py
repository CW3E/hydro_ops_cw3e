"""Submit one audited activation and serial live commissioning updates."""
import argparse
import json
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--handoff', required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    active = subprocess.check_output(['squeue', '--me', '--noheader', '--format=%j'], text=True)
    if any(name.startswith(('obs-cdec-', 'obs-usgs-', 'obs-stations-', 'obs-station-'))
           for name in active.splitlines()):
        raise ValueError('An observation job is active; inspect before cutover')
    ledger = root/'observations/status/stations/cutover-submission.json'
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open('x') as handle:
        handle.write('[]\n')
    rows = []
    env = dict(os.environ)
    env.pop('SBATCH_RESERVATION', None)

    def submit(cmd, stage):
        rows.append({'stage': stage, 'status': 'submission_uncertain', 'command': cmd})
        atomic_json(ledger, rows)
        job = subprocess.check_output(['sbatch', '--parsable', *cmd], cwd=root, env=env, text=True).strip().split(';')[0]
        if not job.isdecimal():
            raise ValueError('Unexpected sbatch response')
        rows[-1].update(status='submitted', job_id=job)
        atomic_json(ledger, rows)
        print(json.dumps(rows[-1]), flush=True)
        return job

    prior = submit(['slurm/cutover_station_collection.sh', '--catalog', args.catalog,
                    '--handoff', args.handoff], 'cutover')
    for selection in ('cdec-fnf', 'cdec-swe', 'usgs'):
        prior = submit([f'--dependency=afterok:{prior}', f'--job-name=obs-stations-commission-{selection}',
                        'slurm/update_station_collection.sh', '--selection', selection,
                        *([] if selection == 'cdec-fnf' else ['--representative'])], selection)


if __name__ == '__main__':
    main()
