"""Read-only year-sharded PRISM mismatch screening using monthly forcing summaries.

Correct summary endpoint offsets with boundary hours before comparing PRISM totals.
"""
import calendar
import json
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
from hydro_ops.wrf_hydro.production import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    year = 1981 + int(os.environ['SLURM_ARRAY_TASK_ID'])
    job = os.environ['SLURM_ARRAY_JOB_ID']
    output = root / f'nwm/analysis/cnrfc/retro/climatology/prism-extent-{job}'
    output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'year': year, 'months': [], 'missing_months': [],
              'method': 'Monthly summaries shifted by exact boundary-hour correction to production PRISM windows'}
    started = time.monotonic()
    try:
        weights = root / 'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
        op = ConservativeOperator.from_cdo(weights)
        with Dataset(weights) as ds:
            nx = int(ds['src_grid_dims'][0]); shape = tuple(int(x) for x in ds['dst_grid_dims'][:][::-1])
            lat = np.rad2deg(ds['dst_grid_center_lat'][:]).reshape(shape)
            lon = (np.rad2deg(ds['dst_grid_center_lon'][:]).reshape(shape)+180) % 360 - 180
        y, x = op.source_index // nx, op.source_index % nx
        inside = (y >= 1252) & (y < 2595) & (x >= 70) & (x < 927)
        local = (y[inside]-1252)*857 + x[inside]-70
        dst, weight, size = op.target_index[inside], op.weight[inside], op.target_size
        total_weight = np.bincount(op.target_index, weights=op.weight, minlength=size)
        partial = np.bincount(dst, weights=weight, minlength=size)
        full = (total_weight > 0) & (partial >= total_weight*.999999)
        del op, y, x, inside

        def hours(day, selection):
            p = root / 'forcing/outputs/cnrfc/retro/hourly' / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            with Dataset(p) as ds:
                ts = num2date(ds['time'][:], ds['time'].units)
                if len(ts) != 24 or [t.hour for t in ts] != list(range(24)):
                    raise ValueError(f'Incomplete boundary hours: {p}')
                return np.sum(np.ma.asarray(ds['RAINRATE'][selection], dtype='f8').filled(np.nan), axis=0)*3600

        accumulated = np.zeros(size); target_total = np.zeros(size); valid_count = np.zeros(size, dtype='i4')
        monthly_ratios = []
        for month in range(1, 13):
            first = date(year, month, 1); length = calendar.monthrange(year, month)[1]
            last = date(year, month, length); following = last+timedelta(days=1)
            path = root / f'forcing/outputs/cnrfc/retro/monthly/{year}/{year}{month:02d}.LDASIN_DOMAIN1.monthly'
            if not path.exists():
                report['missing_months'].append(month); continue
            with Dataset(path) as ds:
                if ds.day_definition != 'model_interval' or ds.aggregation_sample_count != length*24:
                    raise ValueError('Unexpected summary period')
                depth = np.ma.asarray(ds['RAINRATE'][0], dtype='f8').filled(np.nan)*length*86400
            # Model summary covers first-day 01 through next-month 00. PRISM label
            # windows cover previous-month last-day 12 through current last-day 11.
            depth += hours(first-timedelta(days=1), slice(12, 24)) + hours(first, slice(0, 1))
            depth -= hours(last, slice(12, 24)) + hours(following, slice(0, 1))
            if np.nanmin(depth) < -.01:
                raise ValueError(f'Negative boundary-corrected total: {first}; possible stale summary')
            depth = np.maximum(depth, 0)
            linked = depth.reshape(-1)[local]; finite = np.isfinite(linked)
            denominator = np.bincount(dst[finite], weights=weight[finite], minlength=size)
            actual = np.divide(np.bincount(dst[finite], weights=weight[finite]*linked[finite], minlength=size),
                               denominator, out=np.full(size, np.nan), where=denominator > 0)
            target = np.zeros(size)
            for day_number in range(1, length+1):
                day = date(year, month, day_number)
                prism = root / 'forcing/inputs/oregon_state/prism/an/4km/daily/ppt' / day.strftime('%Y/%m') / f'prism_ppt_us_25m_{day:%Y%m%d}.nc'
                with Dataset(prism) as ds:
                    target += np.ma.asarray(ds['ppt'][0], dtype='f8').filled(np.nan).reshape(-1)
                    if day_number == 1:
                        np.testing.assert_allclose(ds['lat'][:], lat[:, 0], atol=1e-5)
                        np.testing.assert_allclose(ds['lon'][:], lon[0], atol=1e-5)
            good = full & (denominator >= partial*.999999) & np.isfinite(actual) & np.isfinite(target)
            wet = good & (target > 10)
            ratio = np.divide(actual, target, out=np.full(size, np.nan), where=wet)
            monthly_ratios.append(ratio.reshape(shape).astype('f4'))
            accumulated[good] += actual[good]; target_total[good] += target[good]; valid_count[good] += 1
            report['months'].append({'month': month, 'cells': int(good.sum()), 'wet_cells': int(wet.sum()),
                                     'forcing_mean_mm': float(np.mean(actual[good])),
                                     'prism_mean_mm': float(np.mean(target[good])),
                                     'ratio_of_means': float(np.sum(actual[good])/np.sum(target[good])),
                                     'wet_fraction_below_half': float(np.mean(ratio[wet] < .5)),
                                     'wet_fraction_nearly_zero': float(np.mean(actual[wet] < .01)),
                                     'median_wet_ratio': float(np.nanmedian(ratio))})
            write_json(output / f'{year}.json', report)
            print(json.dumps(report['months'][-1]), flush=True)
        if not report['months']:
            raise ValueError('No monthly summaries available')
        good = full & (valid_count == len(report['months']))
        wet = good & (target_total > 10)
        ratio = np.divide(accumulated, target_total, out=np.full(size, np.nan), where=wet)
        report.update(status='passed', compared_months=len(report['months']),
                      forcing_mean_mm=float(np.mean(accumulated[good])), prism_mean_mm=float(np.mean(target_total[good])),
                      ratio_of_means=float(np.sum(accumulated[good])/np.sum(target_total[good])),
                      wet_fraction_below_half=float(np.mean(ratio[wet] < .5)),
                      wet_fraction_nearly_zero=float(np.mean(accumulated[wet] < .01)),
                      median_wet_ratio=float(np.nanmedian(ratio)))
        np.savez_compressed(output / f'{year}.npz', lat=lat, lon=lon, ratio=ratio.reshape(shape),
                            monthly_ratios=np.stack(monthly_ratios), months=[m['month'] for m in report['months']])
        fig, ax = plt.subplots(figsize=(6, 7), constrained_layout=True)
        m = ax.pcolormesh(lon, lat, ratio.reshape(shape), shading='auto', cmap='BrBG', vmin=0, vmax=2)
        ax.set(xlim=(-125, -113.5), ylim=(31.5, 44), title=f'{year}: published precipitation / PRISM\n{len(report["months"])} months; targets >10 mm')
        ax.set_aspect(1.27); fig.colorbar(m, ax=ax, shrink=.8)
        fig.savefig(output / f'{year}.png', dpi=120); plt.close(fig)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic()-started
        write_json(output / f'{year}.json', report)


if __name__ == '__main__':
    main()
