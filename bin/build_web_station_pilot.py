"""Build a bounded SWE pilot or --two-layers --full-history publication snapshot.

The historical command name is retained for existing submission scripts.
This exports locally; it does not transfer data or enable routine publication.
"""
import argparse
from pathlib import Path

from hydro_ops.observations.station_operations import operation_lock, recover_pending
from hydro_ops.web.station_pilot import build, build_two_layers

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--two-layers', action='store_true')
parser.add_argument('--full-history', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
if args.full_history:
    if not args.two_layers:
        parser.error('--full-history requires --two-layers')
    with operation_lock(root):
        recover_pending(root)
        print(build_two_layers(root, args.output, full_history=True))
else:
    print((build_two_layers if args.two_layers else build)(root, args.output))
