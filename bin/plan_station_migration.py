"""Inventory station migration candidates without converting or deleting archives."""
import argparse
import os
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations.station_migration import inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot-limit', type=int, default=32)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    output = root/'observations/work/station-migration-inventory'/job/'inventory.json'
    if output.exists():
        raise ValueError('Refusing to overwrite a prior inventory')
    report = inventory(root, pilot_limit=args.pilot_limit)
    report['job_id'] = job
    atomic_json(output, report)
    print(f"{report['counts']}; {report['data_files']} source data files; {report['data_bytes']} bytes")
    print(output)


if __name__ == '__main__':
    main()
