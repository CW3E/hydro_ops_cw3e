"""Submit a frozen, disjoint station conversion array; originals remain authoritative."""
import argparse
import fcntl
import json
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations.station_batches import batches
from hydro_ops.observations.station_conversion import completed_tasks, digest, inside


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--exclude-campaign', type=Path, action='append', default=[])
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    campaign = inside(root, args.campaign)
    if not campaign.is_relative_to(root/'observations/work/station-migration'):
        raise ValueError('Staging-only destination required')
    inventory = inside(root, args.inventory)
    report = json.loads(inventory.read_text())
    if report['schema'] != 'hydro_ops_station_migration_inventory_v1':
        raise ValueError('Unsupported inventory')
    excluded = set()
    for previous in args.exclude_campaign:
        excluded.update(completed_tasks(root, previous, inventory))
    tasks = batches(report, excluded)
    if not tasks:
        raise ValueError('No remaining candidates')
    plan = {'schema': 'hydro_ops_station_migration_array_v1',
            'inventory': str(inventory.relative_to(root)), 'inventory_sha256': digest(inventory),
            'campaign': str(campaign.relative_to(root)), 'excluded_task_ids': sorted(excluded),
            'batches': tasks, 'workers_per_batch': 8, 'concurrency': 2,
            'metadata': {name: {'path': path, 'sha256': digest(root/path)} for name, path in [
                ('fnf', 'observations/static/stations/cdec/20261028T071314Z/stations.json'),
                ('swe', 'observations/static/stations/cdec/swe/20261028T075431Z/stations.json')]}}
    print(json.dumps({'batches': len(tasks), 'histories': sum(map(len, tasks)),
                      'previously_staged': len(excluded), 'concurrency': 2, 'workers_per_batch': 8}))
    if not args.submit:
        return
    campaign.mkdir(parents=True, exist_ok=True)
    with (campaign/'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = campaign/'submission.json'
        if receipt.exists():
            raise ValueError('Submission already attempted; inspect receipt before retrying')
        plan_path = campaign/'plan.json'
        if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
            raise ValueError('Existing plan differs')
        atomic_json(plan_path, plan)
        command = ['sbatch', '--parsable', f'--array=0-{len(tasks)-1}%2',
                   '--job-name=obs-station-full-staged-migration',
                   'slurm/run_station_migration_batch.sh', str(plan_path.relative_to(root)), digest(plan_path)]
        atomic_json(receipt, {'status': 'submitting', 'command': command, 'production_modified': False})
        env = dict(os.environ)
        env.pop('SBATCH_RESERVATION', None)
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False)
        atomic_json(receipt, {'status': 'submitted' if result.returncode == 0 else 'submission_failed',
                             'command': command, 'returncode': result.returncode,
                             'stdout': result.stdout, 'stderr': result.stderr, 'production_modified': False})
        print(result.stdout, end='')
        if result.returncode:
            raise SystemExit(result.stderr)


if __name__ == '__main__':
    main()
