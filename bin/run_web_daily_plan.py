"""Daily full-history rendering with explicitly configured optional live publication."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from hydro_ops.web.globus_transfer import atomic_json, lock
from hydro_ops.web.live_cycle import activate_daily, publish
from hydro_ops.web.schedule import request_from_receipts

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=Path, required=True)
    parser.add_argument('--mode', choices=('plan-only', 'publish'), default='plan-only')
    parser.add_argument('--activate-daily-after-success', action='store_true')
    args = parser.parse_args()
    job = os.environ['SLURM_JOB_ID']
    if not job.isdecimal():
        raise ValueError('SLURM required')
    control = ROOT / 'web/status/daily'
    control.mkdir(parents=True, exist_ok=True)
    with lock(control / 'operational-cycle.lock'):
        run(args, job)


def run(args, job):
    initial_config = json.loads((ROOT / 'config/web_daily.json').read_text())
    if args.activate_daily_after_success and (args.mode != 'publish' or initial_config != {'enabled': False, 'mode': 'publish'}):
        raise ValueError('Activation only allowed for a disabled live commissioning run')
    request = request_from_receipts(ROOT, json.loads(args.jobs.read_text()))
    if args.mode == 'publish':
        request.update(test=False, operational_mode='publish')
    folder = ROOT / 'web/work/daily-integration' / f'job-{job}'
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / 'request.json'
    atomic_json(path, request)
    command = [sys.executable, str(ROOT / 'bin/test_web_daily_coordinator.py')]
    subprocess.run(command + ['gate', '--request', str(path)], check=True)
    subprocess.run(command + ['run', '--request', str(path), '--gate-job', job], check=True)
    if args.mode == 'publish':
        result = publish(ROOT, path, folder / f'run-{job}/coordinator-acceptance.json')
        if args.activate_daily_after_success:
            activate_daily(ROOT, initial_config, result)
            atomic_json(folder / 'activation.json', {'status': 'passed', 'dailyEnabled': True,
                                                    'job_id': job, 'liveAcceptance': result})


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- avoid SDK credential details in logs
        raise SystemExit(f'{type(error).__name__}: daily web cycle stopped; inspect saved receipts and deployment state.') from None
