"""Verify and consolidate accepted batches, without production cutover."""
import argparse
import json
import os
from pathlib import Path

from hydro_ops.observations.station_collection import finalize
from hydro_ops.observations.station_conversion import digest, inside


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--prior-campaign', action='append', default=[])
    parser.add_argument('--catalog', required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    plan_path = inside(root, args.plan)
    if digest(plan_path) != args.plan_sha256:
        raise ValueError('Migration plan changed')
    plan = json.loads(plan_path.read_text())
    if digest(inside(root, plan['inventory'])) != plan['inventory_sha256']:
        raise ValueError('Frozen inventory changed')
    campaigns = args.prior_campaign + [str(Path(plan['campaign'])/f'batch-{i:03d}')
                                       for i in range(len(plan['batches']))]
    print(json.dumps(finalize(root, plan['inventory'], campaigns, args.catalog), indent=2))


if __name__ == '__main__':
    main()
