"""Execute one frozen station migration batch and verify resumability."""
import argparse
import json
import os
from pathlib import Path

from hydro_ops.observations.station_conversion import digest, inside, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('plan_sha256')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    plan_path = inside(root, args.plan)
    if digest(plan_path) != args.plan_sha256:
        raise ValueError('Array plan changed')
    plan = json.loads(plan_path.read_text())
    if plan['schema'] != 'hydro_ops_station_migration_array_v1':
        raise ValueError('Unsupported plan')
    if digest(inside(root, plan['inventory'])) != plan['inventory_sha256']:
        raise ValueError('Frozen inventory changed')
    for item in plan['metadata'].values():
        if digest(inside(root, item['path'])) != item['sha256']:
            raise ValueError('Frozen station metadata changed')
    index = int(os.environ['SLURM_ARRAY_TASK_ID'])
    if not 0 <= index < len(plan['batches']):
        raise ValueError('Array index outside plan')
    campaign = inside(root, plan['campaign'])/f'batch-{index:03d}'
    report = run(root, campaign, Path(plan['inventory']), Path(plan['metadata']['fnf']['path']),
                 Path(plan['metadata']['swe']['path']), workers=plan['workers_per_batch'],
                 task_ids=plan['batches'][index], verify_resume=True)
    print(f"batch {index}: {report['status']} in {report['seconds']:.1f}s")
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
