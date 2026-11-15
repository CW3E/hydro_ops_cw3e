"""Prepare or publish incremental CDEC web histories after an exact acquisition cycle."""
import argparse
import json
import os
from pathlib import Path

from hydro_ops.web.station_cycle import commission, run

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--selection', required=True, choices=('cdec-fnf', 'cdec-swe'))
parser.add_argument('--report', required=True, type=Path)
parser.add_argument('--publish', action='store_true')
parser.add_argument('--retry-failed', action='store_true')
parser.add_argument('--refresh-metadata', action='store_true', help='Republish current collection metadata even when all station data are unchanged')
parser.add_argument('--timeout', type=int, default=1800)
parser.add_argument('--commission-station', help='Force one isolated local re-export, verify byte equality; no remote writes')
args = parser.parse_args()
if args.commission_station:
    if args.publish:
        parser.error('Commissioning never publishes')
    result = commission(Path(__file__).resolve().parents[1], args.selection, args.report,
                        args.commission_station, os.environ['SLURM_JOB_ID'])
else:
    result = run(Path(__file__).resolve().parents[1], args.selection, args.report,
                 publish=args.publish, timeout=args.timeout, retry_failed=args.retry_failed,
                 refresh_metadata=args.refresh_metadata)
print(json.dumps({k: v for k, v in result.items() if k not in ('pins', 'config')}))
