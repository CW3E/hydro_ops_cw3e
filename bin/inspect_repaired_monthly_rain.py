#!/usr/bin/env python3
"""Read-only before/after monthly rainfall maps; run on a compute node."""
import calendar
import json
import os
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import PowerNorm
from netCDF4 import Dataset


def values(variable, key):
    return np.ma.asarray(variable[key], dtype='f8').filled(np.nan)


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    out = root/'forcing/work/monthly-rain-visual-check'/os.environ['SLURM_JOB_ID']
    out.mkdir(parents=True, exist_ok=False)
    wave = root/'forcing/work/prism-repair-wave6-201101-202012/summary-production'
    report = {'purpose': 'Spatial-pattern inspection, not exact PRISM time-window audit',
              'months': []}
    for stamp in ('201301', '201401', '201402', '201501', '201503'):
        year, month = int(stamp[:4]), int(stamp[4:])
        seconds = calendar.monthrange(year, month)[1]*86400
        name = f'{stamp}.LDASIN_DOMAIN1.monthly'
        current = root/f'forcing/outputs/cnrfc/retro/monthly/{year}'/name
        original = wave/stamp/f'originals/monthly/{year}'/name
        status = json.loads((wave/stamp/'status.json').read_text())
        if status['status'] != 'passed':
            raise ValueError(f'Unaccepted summary: {stamp}')
        with Dataset(current) as ds:
            new = values(ds['RAINRATE'], 0)*seconds
            lat, lon = values(ds['lat'], slice(None)), values(ds['lon'], slice(None))
        # Recorded CNRFC crop; coordinate assertions prevent silently using a changed domain.
        crop = (slice(1252, 2595), slice(70, 927))
        with Dataset(original) as ds:
            np.testing.assert_allclose(values(ds['lat'], crop), lat, atol=1e-5)
            np.testing.assert_allclose(values(ds['lon'], crop), lon, atol=1e-5)
            old = values(ds['RAINRATE'], (0, *crop))*seconds
        prism = None
        for day in range(1, calendar.monthrange(year, month)[1]+1):
            path = root/f'forcing/inputs/oregon_state/prism/an/4km/daily/ppt/{year}/{month:02d}/prism_ppt_us_25m_{stamp}{day:02d}.nc'
            with Dataset(path) as ds:
                plat, plon = ds['lat'][:], ds['lon'][:]
                iy = np.flatnonzero((plat >= 31) & (plat <= 44))
                ix = np.flatnonzero((plon >= -125) & (plon <= -113))
                p = values(ds['ppt'], (0, slice(iy.min(), iy.max()+1), slice(ix.min(), ix.max()+1)))
                prism = p if prism is None else prism+p
        good = np.isfinite(new) & np.isfinite(old)
        old = np.where(good, old, np.nan)
        new = np.where(good, new, np.nan)
        vmax = max(float(np.nanpercentile(new, 99)), float(np.nanpercentile(old, 99)), 1)
        fig, axes = plt.subplots(1, 3, figsize=(15, 8), constrained_layout=True)
        for ax, field, x, y, title in (
            (axes[0], old, lon, lat, 'Before repair'),
            (axes[1], new, lon, lat, 'Repaired published summary'),
            (axes[2], prism, plon[ix], plat[iy], 'PRISM daily totals (different day boundary)'),
        ):
            im = ax.pcolormesh(x, y, field, shading='auto', cmap='YlGnBu',
                               norm=PowerNorm(.5, vmin=0, vmax=vmax), rasterized=True)
            ax.set(xlim=(-125, -113.5), ylim=(31.5, 44), title=title,
                   xlabel='Longitude', ylabel='Latitude', aspect=1.27)
        fig.colorbar(im, ax=axes, label='Monthly precipitation depth (mm); square-root color scale',
                     shrink=.65, extend='max')
        fig.suptitle(f'CNRFC precipitation | {year}-{month:02d} | common valid forcing cells')
        fig.savefig(out/f'{stamp}.png', dpi=150)
        plt.close(fig)
        report['months'].append({'month': stamp, 'current': str(current), 'original': str(original),
                                 'common_cells': int(good.sum()),
                                 'old_mean_mm': float(np.nanmean(old)),
                                 'new_mean_mm': float(np.nanmean(new)),
                                 'map': str(out/f'{stamp}.png')})
        (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(stamp, flush=True)


if __name__ == '__main__':
    main()
