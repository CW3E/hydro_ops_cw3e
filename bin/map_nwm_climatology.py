"""Create full-domain rank-map previews from accepted CNRFC percentile tiles."""
import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from benchmark_nwm_climatology import digest, identity, publish, write_history
from benchmark_nwm_history_stream import derive
from matplotlib.colors import BoundaryNorm
from netCDF4 import Dataset

from hydro_ops.wrf_hydro.climatology import PERCENTILES, percentile_rank
from hydro_ops.wrf_hydro.production import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--array-job', type=int, required=True)
    parser.add_argument('--domain', choices=('cnrfc', 'ucrb'), default='cnrfc')
    parser.add_argument('--dates', nargs='+', type=date.fromisoformat,
                        default=[date(2015, 4, 1), date(2023, 4, 1), date(2025, 9, 30)])
    args = parser.parse_args()
    domain = args.domain
    root = Path(__file__).resolve().parents[1]
    job, task, user = os.environ['SLURM_JOB_ID'], int(os.environ['SLURM_ARRAY_TASK_ID']), os.environ['SLURM_JOB_USER']
    day = args.dates[task]
    array_job = os.environ['SLURM_ARRAY_JOB_ID']
    work = Path(f'/scratch/{user}/job_{job}/rank-map')
    work.mkdir(parents=True, exist_ok=False)
    output = root / f'nwm/analysis/{domain}/retro/climatology/wy1980-2025/maps-{array_job}'
    state = root / f'nwm/status/{domain}/climatology/maps-{array_job}'
    state.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'domain': domain, 'date': str(day), 'climatology_array': args.array_job}
    started = time.monotonic()
    try:
        acceptance = json.loads((root / f'nwm/status/{domain}/climatology/tiles-{args.array_job}/acceptance.json').read_text())
        if acceptance['status'] != 'passed':
            raise ValueError('Climatology acceptance required')
        if acceptance.get('source_model_campaign') != 'repaired_rain_retro_v1':
            raise ValueError('Corrected-run climatology required; old tables must not be mixed with reruns')
        if acceptance.get('domain', 'cnrfc') != domain:
            raise ValueError('Climatology domain mismatch')
        mask_path = root / f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
        if digest(mask_path) != acceptance['mask_sha256']:
            raise ValueError('Mask changed')
        monthly = root / f'nwm/runs/{domain}/retro/repaired_rain_retro_v1' / day.strftime('%Y%m') / 'accepted.json'
        if not json.loads(monthly.read_text()).get('passed'):
            raise ValueError('Unaccepted model month')
        source = root / f'nwm/outputs/{domain}/retro/daily' / day.strftime('%Y/%m/%Y%m%d.LDASOUT_DOMAIN1.daily')
        before = identity(source)
        history_plan = json.loads((root / f'nwm/status/{domain}/climatology' /
                                   f'history-stream-{acceptance["history_job"]}/plan.json').read_text())
        source_pin = next((p for p in history_plan['sources'] if root/p['path'] == source), None)
        if source_pin is None or source_pin['identity'] != before:
            raise ValueError('Map source differs from the accepted corrected climatology history')
        with Dataset(mask_path) as ds:
            active = np.asarray(ds['model_mask'][:], dtype=bool)
            lat, lon = ds['lat'][:], ds['lon'][:]
        with Dataset(source) as ds:
            values = derive(ds, day, active)
            grid = {'active': active, 'window': [0, active.shape[0], 0, active.shape[1]],
                    'x': ds['x'][:], 'y': ds['y'][:], 'lat': lat, 'lon': lon,
                    'crs': {k: ds['crs'].getncattr(k) for k in ds['crs'].ncattrs() if k != '_FillValue'}}
        ranks = np.full_like(values, np.nan)
        counts = np.zeros(values.shape, dtype='i4')
        covered = np.zeros(active.shape, bool)
        doy = (date(2000, day.month, day.day)-date(2000, 1, 1)).days
        for item in acceptance['tiles']:
            path = Path(item['path'])
            original = identity(path)
            y0, y1, x0, x1 = item['window']; yy, xx = slice(y0, y1), slice(x0, x1)
            if covered[yy, xx].any():
                raise ValueError('Overlapping tile windows')
            covered[yy, xx] = True
            with Dataset(path) as ds:
                if json.loads(ds.tile_window) != item['window']:
                    raise ValueError('Tile window mismatch')
                np.testing.assert_array_equal(ds['percentile'][:], PERCENTILES)
                np.testing.assert_allclose(ds['lat'][:], lat[yy, xx], atol=1e-6)
                np.testing.assert_allclose(ds['lon'][:], lon[yy, xx], atol=1e-6)
                for j, name in enumerate(('SOIL_WATER', 'SNEQV')):
                    q = np.ma.asarray(ds[name][doy]).filled(np.nan)
                    ranks[j, yy, xx] = percentile_rank(values[j, yy, xx], q,
                                                      swe_minimum=1. if j == 1 else None)
                    counts[j, yy, xx] = ds[name+'_sample_count'][doy]
            if identity(path) != original:
                raise ValueError('Tile changed during map calculation')
        if not covered[active].all() or identity(source) != before:
            raise ValueError('Incomplete coverage or changed source')
        for j in range(2):
            eligible = active & np.isfinite(values[j])
            if j == 1:
                eligible &= values[j] > 1
            if not np.isfinite(ranks[j][eligible]).all():
                raise ValueError('Missing ranks at eligible cells')
        target = work / f'{day:%Y%m%d}.land_percentiles.nc'
        write_history(target, grid, [day], values[None])
        with Dataset(target, 'a') as ds:
            for j, name in enumerate(('SOIL_WATER', 'SNEQV')):
                v = ds.createVariable(name+'_PERCENTILE', 'f4', ('time', 'y', 'x'), fill_value=-9999., zlib=True)
                v[0] = np.ma.masked_invalid(ranks[j]); v.units = '%'; v.coordinates = 'lat lon'; v.grid_mapping = 'crs'
                v.long_name = name+' approximate climatological percentile rank'
                v.comment = 'Table inversion; clipped tails 0.5/99.5; duplicate thresholds use mean table levels'
                c = ds.createVariable(name+'_SAMPLE_COUNT', 'i4', ('y', 'x'), zlib=True)
                c[:] = counts[j]
            ds.swe_rank_minimum_mm = 1.
            ds.reference_window_days = 31
            ds.climatology_array_job = str(args.array_job)
            ds.product_status = 'visual-review preview; model-relative ranks, not observational validation'
        boundaries = gpd.read_file(root / f'nwm/static/domains/{domain}/boundaries/{domain}_boundaries.gpkg',
                                   layer='cnrfc_hu18_union' if domain == 'cnrfc' else 'ucrb_reporting').to_crs(4326)
        fig, axes = plt.subplots(2, 2, figsize=(13, 13), constrained_layout=True)
        levels = [0, 2, 5, 10, 20, 30, 70, 80, 90, 95, 98, 100]
        cmap = plt.get_cmap('BrBG', len(levels)-1)
        for j, label in enumerate(('Column soil water', 'Snow water equivalent')):
            for k in range(2):
                ax = axes[j, k]
                ax.set_facecolor('#eeeeee')
                if k == 0:
                    m = ax.pcolormesh(lon, lat, values[j], shading='auto', rasterized=True,
                                      cmap='viridis' if j == 0 else 'Blues', vmin=0, vmax=1000 if j == 0 else 1500)
                    fig.colorbar(m, ax=ax, shrink=.82, label='Daily-mean water storage (mm)', extend='max')
                else:
                    m = ax.pcolormesh(lon, lat, ranks[j], shading='auto', rasterized=True,
                                      cmap=cmap, norm=BoundaryNorm(levels, cmap.N))
                    fig.colorbar(m, ax=ax, shrink=.82, ticks=[0, 5, 10, 20, 30, 70, 80, 90, 95, 100], label='Percentile rank (%)')
                boundaries.boundary.plot(ax=ax, color='#333333', linewidth=.6)
                ax.set_xlim(float(lon[active].min())-.1, float(lon[active].max())+.1)
                ax.set_ylim(float(lat[active].min())-.1, float(lat[active].max())+.1)
                ax.set_aspect(1/np.cos(np.deg2rad(38.)))
                ax.set_xlabel('Longitude'); ax.set_ylabel('Latitude')
                ax.set_title(label+(' | amount' if k == 0 else ' | percentile'), fontsize=13)
        fig.suptitle(f'{domain.upper()} land conditions | {day}\nDaily model means; climatology WY1980–WY2025, ±15-day window', fontsize=17)
        fig.supxlabel('Brown: lower than usual • Green: higher than usual • SWE rank blank at ≤1 mm\n'
                      'Outline: reporting region; data include active buffer cells. Model-relative, not observed conditions.', fontsize=10)
        png = work / f'{day:%Y%m%d}.land_percentiles.png'
        fig.savefig(png, dpi=150); plt.close(fig)
        report.update(status='passed', soil_rank_cells=int(np.isfinite(ranks[0]).sum()),
                      swe_rank_cells=int(np.isfinite(ranks[1]).sum()),
                      artifacts=[publish(p, output / p.name) for p in (png, target)])
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic()-started
        write_json(state / f'{day:%Y%m%d}.json', report)


if __name__ == '__main__':
    main()
