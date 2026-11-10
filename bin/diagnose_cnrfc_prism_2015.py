"""Read-only audit of published CNRFC rainfall against native PRISM windows."""
import os
import time
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.wrf_hydro.climatology import dates
from hydro_ops.wrf_hydro.production import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    output = root / f'nwm/analysis/cnrfc/retro/climatology/prism-diagnosis-{job}'
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    days = dates(date(2014, 10, 1), date(2015, 3, 31))
    weights = root / 'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
    op = ConservativeOperator.from_cdo(weights)
    with Dataset(weights) as ds:
        nx = int(ds['src_grid_dims'][0])
        shape = tuple(int(x) for x in ds['dst_grid_dims'][:][::-1])
        lat = np.rad2deg(ds['dst_grid_center_lat'][:]).reshape(shape)
        lon = (np.rad2deg(ds['dst_grid_center_lon'][:]).reshape(shape)+180) % 360 - 180
    y = op.source_index // nx; x = op.source_index % nx
    inside = (y >= 1252) & (y < 2595) & (x >= 70) & (x < 927)
    local = (y[inside]-1252)*857 + x[inside]-70
    dst, weight = op.target_index[inside], op.weight[inside]
    total_weight = np.bincount(op.target_index, weights=op.weight, minlength=op.target_size)
    partial_weight = np.bincount(dst, weights=weight, minlength=op.target_size)
    full = (total_weight > 0) & (partial_weight >= total_weight*.999999)
    size = op.target_size
    del op, y, x, inside
    report = {'status': 'running', 'start': str(days[0]), 'end': str(days[-1]),
              'window': 'published production convention: previous day 12 through labeled day 11 UTC', 'days': []}

    def load(day):
        path = root / 'forcing/outputs/cnrfc/retro/hourly' / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        with Dataset(path) as ds:
            ts = num2date(ds['time'][:], ds['time'].units)
            if len(ts) != 24 or [t.hour for t in ts] != list(range(24)):
                raise ValueError('Invalid hourly axis')
            a = np.ma.asarray(ds['RAINRATE'][:], dtype='f8').filled(np.nan)*3600
            halves = np.stack([np.sum(a[:12], axis=0), np.sum(a[12:], axis=0)])
        return halves

    cumulative = np.zeros(size); target_sum = np.zeros(size); n = np.zeros(size, dtype='i4')
    dry_wet = np.zeros(size, dtype='i4'); bad = np.zeros(size, dtype='i4')
    previous = load(days[0]-timedelta(days=1))
    for day in days:
        current = load(day)
        raw = (previous[1]+current[0]).reshape(-1); previous = current
        linked = raw[local]; valid = np.isfinite(linked)
        denominator = np.bincount(dst[valid], weights=weight[valid], minlength=size)
        estimate = np.divide(np.bincount(dst[valid], weights=weight[valid]*linked[valid], minlength=size),
                             denominator, out=np.full(size, np.nan), where=denominator > 0)
        path = root / 'forcing/inputs/oregon_state/prism/an/4km/daily/ppt' / day.strftime('%Y/%m') / f'prism_ppt_us_25m_{day:%Y%m%d}.nc'
        with Dataset(path) as ds:
            prism = np.ma.asarray(ds['ppt'][0], dtype='f8').filled(np.nan).reshape(-1)
            np.testing.assert_allclose(ds['lat'][:], lat[:, 0], atol=1e-5)
            np.testing.assert_allclose(ds['lon'][:], lon[0], atol=1e-5)
        good = full & (denominator >= partial_weight*.999999) & np.isfinite(estimate) & np.isfinite(prism)
        cumulative[good] += estimate[good]; target_sum[good] += prism[good]; n[good] += 1
        dry_wet[good] += (estimate[good] < .01) & (prism[good] > 1.)
        bad[good] += np.abs(estimate[good]-prism[good]) > np.maximum(prism[good], 1)*.01
        report['days'].append({'date': str(day), 'cells': int(good.sum()),
                               'mean_forcing_mm': float(np.mean(estimate[good])),
                               'mean_prism_mm': float(np.mean(prism[good])),
                               'dry_forcing_wet_prism_cells': int(np.sum(good & (estimate < .01) & (prism > 1.)))})
        if len(report['days']) % 15 == 0:
            write_json(output / 'status.json', report)
            print(str(day), flush=True)
    good = full & (n == len(days))
    ratio = np.divide(cumulative, target_sum, out=np.full(size, np.nan), where=good & (target_sum > 10))
    report.update(status='passed', full_period_cells=int(good.sum()),
                  cumulative_forcing_mean_mm=float(np.mean(cumulative[good])),
                  cumulative_prism_mean_mm=float(np.mean(target_sum[good])),
                  ratio_percentiles=dict(zip(['p01', 'p10', 'p50', 'p90', 'p99'],
                                             np.nanpercentile(ratio, [1, 10, 50, 90, 99]).tolist(), strict=True)),
                  cells_with_dry_wet_mismatch=int(np.sum(good & (dry_wet > 0))),
                  elapsed_seconds=time.monotonic()-started)
    np.savez_compressed(output / 'diagnostics.npz', lat=lat, lon=lon, valid=good.reshape(shape),
                        forcing_mm=cumulative.reshape(shape), prism_mm=target_sum.reshape(shape),
                        ratio=ratio.reshape(shape), dry_wet_days=dry_wet.reshape(shape), bad_days=bad.reshape(shape))
    fig, axes = plt.subplots(2, 2, figsize=(12, 12), constrained_layout=True)
    plots = [(cumulative, 'Published forcing total (mm)', 'Blues', 0, 1200),
             (target_sum, 'PRISM target total (mm)', 'Blues', 0, 1200),
             (ratio, 'Forcing / PRISM', 'BrBG', 0, 2),
             (dry_wet, 'Days forcing <0.01 mm but PRISM >1 mm', 'magma', 0, 60)]
    for ax, (field, title, cmap, vmin, vmax) in zip(axes.flat, plots, strict=True):
        m = ax.pcolormesh(lon, lat, np.where(good, field, np.nan).reshape(shape), shading='auto',
                          cmap=cmap, vmin=vmin, vmax=vmax)
        ax.set(xlim=(-125, -113.5), ylim=(31.5, 44), title=title, xlabel='Longitude', ylabel='Latitude')
        ax.set_aspect(1.27); fig.colorbar(m, ax=ax, shrink=.8, extend='max')
    fig.suptitle('CNRFC rainfall audit | 2014-10-01 through 2015-03-31\nPublished daily windows aggregated onto the native PRISM grid')
    fig.savefig(output / 'precipitation_comparison.png', dpi=140); plt.close(fig)
    write_json(output / 'status.json', report)


if __name__ == '__main__':
    main()
