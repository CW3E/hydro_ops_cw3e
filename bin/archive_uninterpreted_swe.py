"""Retire unsupported SWE combinations to a checksum-verified offline archive."""
import json
from pathlib import Path

from hydro_ops.observations.swe_archive import run

if __name__ == '__main__':
    print(json.dumps(run(Path(__file__).resolve().parents[1]), indent=2))
