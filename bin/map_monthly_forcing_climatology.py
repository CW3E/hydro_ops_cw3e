"""Plot monthly forcing values and table-derived ranks without changing archives."""
import argparse
import json
import os
import time
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import netCDF4
import numpy as np
from matplotlib.colors import BoundaryNorm
from produce_monthly_forcing_climatology import identity

from hydro_ops.wrf_hydro.climatology import percentile_rank


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task', type=int, default=int(os.environ.get('SLURM_ARRAY_TASK_ID', '0')))
    args = p.parse_args()
    domain, job = [('cnrfc', 5263647), ('ucrb', 5263648)][args.task // 3]
    key = ['201504', '202304', '202509'][args.task % 3]
    month = int(key[4:])
    root = Path(__file__).resolve().parents[1]
    table_dir = root / f'forcing/analysis/{domain}/retro/monthly-climatology/wy1980-2025/job_{job}'
    acceptance = json.loads((table_dir / 'report.json').read_text())
    table = root / acceptance['output']
    if acceptance['status'] != 'passed' or identity(table) != acceptance['output_identity']:
        raise ValueError('Accepted, unchanged climatology required')
    source = root / f'forcing/outputs/{domain}/retro/monthly/{key[:4]}/{key}.LDASIN_DOMAIN1.monthly'
    pin = next(r for r in acceptance['sources'] if r['year'] == int(key[:4]) and r['month'] == month)
    if identity(source) != pin['identity']:
        raise ValueError('Source differs from reference-history snapshot')
    output = table_dir / f'maps-{os.environ.get("SLURM_ARRAY_JOB_ID", "manual")}'
    output.mkdir(exist_ok=True)
    report = {'status': 'running', 'domain': domain, 'month': key, 'table': str(table), 'source': str(source)}
    start = time.monotonic()
    try:
        values, ranks = {}, {}
        with netCDF4.Dataset(table) as ds, netCDF4.Dataset(source) as src:
            lat, lon = ds['lat'][:], ds['lon'][:]
            for coordinate in ('lat', 'lon', 'subset_forcing_mask'):
                np.testing.assert_array_equal(ds[coordinate][:], src[coordinate][:])
            mask = np.asarray(ds['subset_forcing_mask'][:], dtype=bool)
            levels = ds['percentile'][:]
            for name in ('T2D', 'RAINRATE'):
                values[name] = np.ma.asarray(src[name][0], dtype='f8').filled(np.nan)
                q = np.ma.asarray(ds[name][month-1], dtype='f8').filled(np.nan)
                ranks[name] = percentile_rank(values[name], q, levels)
                eligible = mask & np.isfinite(values[name]) & (ds[name+'_sample_count'][month-1] >= acceptance['min_samples'])
                if not np.isfinite(ranks[name][eligible]).all():
                    raise ValueError('Missing eligible ranks')
                if np.any((ranks[name] < .5) | (ranks[name] > 99.5)):
                    raise ValueError('Invalid ranks')
                values[name][~mask] = np.nan
                ranks[name][~mask] = np.nan
                del q
        boundary = gpd.read_file(root / f'nwm/static/domains/{domain}/boundaries/{domain}_boundaries.gpkg',
                                 layer='cnrfc_hu18_union' if domain == 'cnrfc' else 'ucrb_reporting').to_crs(4326)
        fig, axes = plt.subplots(2, 2, figsize=(13, 12), constrained_layout=True)
        bins = [0, 5, 10, 20, 30, 70, 80, 90, 95, 100]
        for row, name in enumerate(('T2D', 'RAINRATE')):
            for col in range(2):
                ax = axes[row, col]
                ax.set_facecolor('#eeeeee')
                if col:
                    cmap = plt.get_cmap('RdBu_r' if name == 'T2D' else 'BrBG', len(bins)-1)
                    m = ax.pcolormesh(lon, lat, ranks[name], shading='auto', cmap=cmap,
                                      norm=BoundaryNorm(bins, cmap.N), rasterized=True)
                    fig.colorbar(m, ax=ax, shrink=.8, ticks=bins, label='Percentile rank (%)')
                else:
                    amount = values[name] - 273.15 if name == 'T2D' else values[name]*86400
                    m = ax.pcolormesh(lon, lat, amount, shading='auto', rasterized=True,
                                      cmap='RdYlBu_r' if name == 'T2D' else 'Blues', vmin=-10 if name == 'T2D' else 0,
                                      vmax=35 if name == 'T2D' else 10)
                    fig.colorbar(m, ax=ax, shrink=.8, extend='both' if name == 'T2D' else 'max',
                                 label='Monthly mean temperature (°C)' if name == 'T2D' else 'Monthly mean precipitation rate (mm/day)')
                boundary.boundary.plot(ax=ax, color='#333333', linewidth=.6)
                ax.set_xlim(float(lon[mask].min())-.1, float(lon[mask].max())+.1)
                ax.set_ylim(float(lat[mask].min())-.1, float(lat[mask].max())+.1)
                ax.set_aspect(1/np.cos(np.deg2rad(float(np.mean(lat[mask])))))
                ax.set_title(('Temperature' if name == 'T2D' else 'Precipitation') + (' | rank' if col else ' | monthly mean'))
                ax.set_xlabel('Longitude'); ax.set_ylabel('Latitude')
        fig.suptitle(f'{domain.upper()} | {key[:4]}-{key[4:]}\nCalendar-month climatology: WY1980–2025 (46 years)', fontsize=18)
        fig.supxlabel('Temperature: blue = cooler, red = warmer • Precipitation: brown = drier, green = wetter\n'
                      'Outline: reporting region; buffer retained. Gray: missing. Ranks include the example year; ties use midranks.', fontsize=11)
        png = output / f'{key}.forcing_percentiles.png'
        fig.savefig(png, dpi=150); plt.close(fig)
        nc = output / f'{key}.forcing_percentiles.nc'
        with netCDF4.Dataset(nc, 'w') as ds:
            ds.createDimension('y', lat.shape[0]); ds.createDimension('x', lat.shape[1])
            for name, data in [('lat', lat), ('lon', lon), *values.items(),
                               *((n+'_PERCENTILE', v) for n, v in ranks.items())]:
                v = ds.createVariable(name, 'f4', ('y', 'x'), zlib=True, complevel=2, fill_value=-9999.)
                v[:] = np.ma.masked_invalid(data)
                v.units = ('K' if name == 'T2D' else 'kg m-2 s-1' if name == 'RAINRATE' else
                           '%' if name.endswith('_PERCENTILE') else 'degrees_north' if name == 'lat' else 'degrees_east')
            ds.source_month = key
            ds.reference_period = '1979-10 through 2025-09'
            ds.rank_method = 'Quantile table inversion; tied thresholds use midranks; tails clipped to 0.5/99.5'
        if identity(source) != pin['identity'] or identity(table) != acceptance['output_identity']:
            raise ValueError('Input changed during mapping')
        report.update(status='passed', png=str(png), netcdf=str(nc))
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic()-start
        (output / f'{key}.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
