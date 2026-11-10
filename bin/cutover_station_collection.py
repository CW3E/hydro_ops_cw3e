"""Activate the verified permanent collection without deleting old archives."""
import argparse
import json
import os
from pathlib import Path

from hydro_ops.observations.station_operations import cutover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--handoff', required=True)
    args = parser.parse_args()
    print(json.dumps(cutover(Path(os.environ['HYDRO_OPS_PROJECT_ROOT']), args.catalog, args.handoff), indent=2))


if __name__ == '__main__':
    main()
