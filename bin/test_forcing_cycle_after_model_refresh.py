"""Launch the forcing-only daily commissioning test after accepted model refreshes.

This launches a coordinator, not an acceptance certificate. Inspect its complete
downstream chain before testing six-hourly or installing any cron schedule.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json


def check_models(state):
    plan_hash = hashlib.sha256((state/'plan.json').read_bytes()).hexdigest()
    reports = {}
    for domain in ('yampa', 'b120', 'ucrb'):
        for stream in ('retro', 'nrt'):
            path = state/domain/stream/'acceptance.json'
            report = json.loads(path.read_text())
            if (report.get('status') != 'complete' or report.get('domain') != domain
                    or report.get('stream') != stream or report.get('plan_sha256') != plan_hash
                    or not report.get('published_files')):
                raise ValueError(f'Model refresh not accepted: {path}')
            reports[f'{domain}/{stream}'] = report['job']
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reconciliation', type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    models = check_models(root/'nwm/status/thermal_refresh_2026_v1')
    reconciliation = json.loads(args.reconciliation.read_text())
    if reconciliation.get('status') != 'applied':
        raise ValueError('Revision reconciliation has not completed')
    output = root/'forcing/work/cron-recommissioning'/os.environ['SLURM_JOB_ID']/'launch.json'
    if output.exists():
        raise ValueError('Launch already attempted; inspect its coordinator before retrying')
    command = [sys.executable, 'bin/update_nwm_forcing.py', '--cycle', 'daily', '--skip-model-runs']
    report = {'status': 'launching', 'model_acceptances': models,
              'reconciliation': str(args.reconciliation.resolve()), 'command': command,
              'cron_installed': False, 'cycle_accepted': False}
    atomic_json(output, report)
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False,
                            env={k: v for k, v in os.environ.items() if k != 'SBATCH_RESERVATION'})
    report.update(status='coordinator_returned_success' if result.returncode == 0 else 'launch_failed',
                  returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    atomic_json(output, report)
    print(json.dumps(report, indent=2))
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
