"""Export all configured daily/monthly CDEC FNF histories to a local web pilot."""
import argparse
from pathlib import Path

from hydro_ops.web.station_fnf import build

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--update-report', type=Path, help='Passed FNF cycle acceptance with receipts matching source hashes')
args = parser.parse_args()
report = build(Path(__file__).resolve().parents[1], args.output, args.update_report)
print({k: report[k] for k in ('status', 'layers')})
