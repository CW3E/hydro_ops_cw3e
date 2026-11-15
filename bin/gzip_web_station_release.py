"""Create a fresh gzip publication from an accepted plain-CSV release."""
import argparse
import json
from pathlib import Path

from hydro_ops.web.station_gzip import package

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(Path(__file__).resolve().parents[1], args.source, args.output), indent=2))
