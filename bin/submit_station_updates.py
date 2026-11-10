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
    args = parser.parse_args()
    print(json.dumps(submit(Path(os.environ['HYDRO_OPS_PROJECT_ROOT']), args.selection,
                            dry_run=args.dry_run, resume_report=args.resume_report,
                            continuations=args.continuations)))


if __name__ == '__main__':
    main()
