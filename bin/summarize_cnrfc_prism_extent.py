"""Summarize completed read-only year screens; missing/failed years stay explicit."""
import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import TwoSlopeNorm

from hydro_ops.wrf_hydro.production import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, nargs='+', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    base = root / 'nwm/analysis/cnrfc/retro/climatology'
    output = base / ('prism-extent-summary-'+'-'.join(map(str, args.jobs)))
    output.mkdir(exist_ok=False)
    rows = {}
    for job in args.jobs:
        for p in (base / f'prism-extent-{job}').glob('*.json'):
            d = json.loads(p.read_text())
            if d['year'] in rows:
                raise ValueError('Duplicate year reports')
            rows[d['year']] = d
    years = list(range(1981, 2026)); a = np.full((len(years), 13), np.nan)
    annual, monthly, failed = [], [], []
    for i, year in enumerate(years):
        d = rows.get(year, {})
        if d.get('status') != 'passed':
            failed.append({'year': year, 'status': d.get('status', 'missing'), 'error': d.get('error')})
            continue
        for m in d['months']:
            monthly.append({'year': year, **m}); a[i, m['month']-1] = m['ratio_of_means']
        a[i, 12] = d['ratio_of_means']
        annual.append({k: d[k] for k in ['year', 'compared_months', 'forcing_mean_mm', 'prism_mean_mm',
                                       'ratio_of_means', 'wet_fraction_below_half', 'wet_fraction_nearly_zero']})
    for name, entries in [('annual', annual), ('monthly', monthly)]:
        if entries:
            with (output / f'{name}.csv').open('w') as f:
                writer = csv.DictWriter(f, fieldnames=list(entries[0])); writer.writeheader(); writer.writerows(entries)
    fig, ax = plt.subplots(figsize=(12, 15), constrained_layout=True)
    m = ax.imshow(a, aspect='auto', cmap='BrBG', norm=TwoSlopeNorm(vmin=0, vcenter=1, vmax=1.2))
    ax.set_yticks(range(len(years)), years)
    ax.set_xticks(range(13), ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec','Year'])
    ax.set_title('CNRFC published precipitation / PRISM\nBoundary-corrected monthly screening; 1 means matching regional totals')
    fig.colorbar(m, ax=ax, label='Ratio of regional mean totals', shrink=.7, extend='max')
    for i in range(len(years)):
        if np.isfinite(a[i,12]):
            ax.text(12, i, f'{a[i,12]:.2f}', ha='center', va='center', fontsize=8)
    fig.savefig(output / 'monthly_ratio_timeline.png', dpi=140); plt.close(fig)
    write_json(output / 'status.json', {'status': 'passed' if not failed else 'incomplete',
                                      'years': annual, 'failed_or_missing': failed,
                                      'scope': 'CNRFC monthly summaries, not yet a national or all-hour publication audit'})


if __name__ == '__main__':
    main()
