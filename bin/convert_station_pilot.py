"""Execute or resume an explicitly bounded, staging-only station conversion."""
import argparse
import json
import os
from pathlib import Path

from hydro_ops.observations.station_conversion import completed_tasks, run, select_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--verify-resume', action='store_true')
    parser.add_argument('--batch-size', type=int, help='Select up to 128 balanced candidates')
    parser.add_argument('--exclude-campaign', type=Path, action='append', default=[])
    args = parser.parse_args()
    if 'SLURM_JOB_ID' not in os.environ:
        raise RuntimeError('Conversion must run in SLURM')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    tasks = None
    if args.exclude_campaign and args.batch_size is None:
        parser.error('--exclude-campaign requires --batch-size')
    if args.batch_size is not None:
        excluded = set()
        for campaign in args.exclude_campaign:
            excluded.update(completed_tasks(root, campaign, args.inventory))
        tasks = select_batch(json.loads((root/args.inventory).read_text()), size=args.batch_size, excluded=excluded)
    report = run(root, root/args.campaign, args.inventory,
                 Path('observations/static/stations/cdec/20261028T071314Z/stations.json'),
                 Path('observations/static/stations/cdec/swe/20261028T075431Z/stations.json'),
                 workers=args.workers, verify_resume=args.verify_resume, task_ids=tasks)
    print(f"{report['status']}: {report['seconds']:.1f} seconds")
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
