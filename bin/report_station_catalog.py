"""Display a pilot station catalog; optional checksum verification is read-only."""
import argparse
import json
import os
from pathlib import Path

from hydro_ops.observations.station_catalog import read_catalog
from hydro_ops.observations.station_operations import active_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', nargs='?', help='Defaults to the activated production collection')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.catalog is None:
        args.catalog = active_catalog(Path(os.environ['HYDRO_OPS_PROJECT_ROOT']))
        if args.catalog is None:
            parser.error('No active station collection; supply a staged catalog explicitly')
    report = read_catalog(args.catalog, verify=args.verify)
    if args.json:
        print(json.dumps({**report, 'member_checksums_verified_now': args.verify}, indent=2))
        return
    print('Member checksums:', 'verified now' if args.verify else 'not rechecked (catalog snapshot only)')
    print(f"{'Provider':<9} {'Station':<18} {'Product':<20} {'Sensor':<8} {'Resolution':<10} {'Rows':>8} {'Numeric':>8} {'Last numeric period':<20}")
    for row in report['entries']:
        print(f"{row['provider']:<9} {row['station_id']:<18} {row['product']:<20} {row['sensor']:<8} "
              f"{row['resolution']:<10} {row['rows']:>8} {row['numeric_rows']:>8} {row['last_numeric_period']!s:<20}")


if __name__ == '__main__':
    main()
