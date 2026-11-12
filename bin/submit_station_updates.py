"""Submit a routine update of one active observation collection product."""
import argparse
import json
import os
from pathlib import Path

from hydro_ops.observations.station_submit import submit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', required=True, choices=('cdec-fnf', 'cdec-swe', 'usgs'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--resume-report', type=Path)
    parser.add_argument('--continuations',type=int,choices=range(4),default=0)
    parser.add_argument('--prepare-workers', type=int, choices=(0, 1, 4), default=None)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--after-success', help='Require successful completion of a commissioning job')
    parser.add_argument('--representative', action='store_true')
    parser.add_argument('--optimized-coordinator', action='store_true')
    parser.add_argument('--usgs-scope', choices=('all', 'regional', 'remainder'), default='all')
    parser.add_argument('--swe-resolutions', nargs='+', choices=('daily', 'monthly', 'hourly', 'event'),
                        default=['daily', 'monthly'], help='CDEC SWE only; hourly/event are on demand')
    args = parser.parse_args()
    print(json.dumps(submit(Path(os.environ['HYDRO_OPS_PROJECT_ROOT']), args.selection,
                            dry_run=args.dry_run, resume_report=args.resume_report,
                            continuations=args.continuations, prepare_workers=args.prepare_workers,
                            limit=args.limit, after_success=args.after_success,
                            representative=args.representative, swe_resolutions=args.swe_resolutions,
                            optimized_coordinator=args.optimized_coordinator, usgs_scope=args.usgs_scope)))


if __name__ == '__main__':
    main()
