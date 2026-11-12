"""Single-pass CNRFC archive extraction to buffered, full-period tile histories.

Isolated SLURM benchmark: never modifies model outputs or existing histories.
"""
import argparse
import json
import os
import re
import resource
import shutil
import time
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from benchmark_nwm_climatology import create_grid, digest, identity, publish
from netCDF4 import Dataset, date2num, num2date

from hydro_ops.wrf_hydro.climatology import THICKNESSES, column_soil_water, dates, tile_windows
from hydro_ops.wrf_hydro.production import write_json

FIELDS = ('SOIL_WATER', 'SNEQV')
UNITS = 'days since 1970-01-01 00:00:00'
MODEL_CAMPAIGN = 'repaired_rain_retro_v1'


def accepted_month(root, month, domain='cnrfc'):
    """Use corrected-run evidence, never the superseded pilot campaign."""
    if domain not in ('cnrfc', 'ucrb'):
        raise ValueError('Unsupported climatology domain')
    folder = root / f'nwm/runs/{domain}/retro' / MODEL_CAMPAIGN / month
    receipt = folder/'accepted.json'
    accepted = json.loads(receipt.read_text())
    if not accepted.get('passed') or accepted.get('stream') != 'retro':
        raise ValueError(f'Unaccepted source month: {month}')
    namelist = folder/'namelist.hrldas'
    layers = re.findall(r'soil_thick_input\(\s*(\d+)\s*\)\s*=\s*([\d.eE+-]+)',
                        namelist.read_text(), re.IGNORECASE)
    actual = [float(v) for _, v in sorted(layers, key=lambda x: int(x[0]))]
    if len(actual) != 4 or not np.allclose(actual, THICKNESSES, rtol=0, atol=1e-12):
        raise ValueError(f'Unexpected soil thicknesses: {month}')
    return [{'path': str(p.relative_to(root)), 'identity': identity(p)} for p in (receipt, namelist)]


def derive(ds, day, active):
    if len(ds.dimensions['time']) != 1 or getattr(ds, 'day_definition', '') != 'model_interval':
        raise ValueError('Not an accepted model-interval daily collection')
    b = ds['time_bounds']
    observed = [t.isoformat() for t in num2date(b[:].reshape(-1), b.units)]
    expected = [datetime.combine(d, datetime.min.time()).isoformat()
                for d in (day, day+timedelta(days=1))]
    if observed != expected:
        raise ValueError(f'Invalid daily bounds for {day}')
    for name, units in [('SOIL_M', 'm3 m-3'), ('SNEQV', 'kg m-2')]:
        if ds[name].units != units or ds[name].cell_methods != 'time: mean':
            raise ValueError(f'Invalid {name} semantics')
    if ds['SOIL_M'].dimensions != ('time', 'y', 'soil_layers_stag', 'x'):
        raise ValueError('Invalid soil dimensions')
    if ds['SNEQV'].dimensions != ('time', 'y', 'x'):
        raise ValueError('Invalid SWE dimensions')
    soil = column_soil_water(ds['SOIL_M'][0])
    swe = np.ma.asarray(ds['SNEQV'][0], dtype='f8').filled(np.nan)
    swe[(swe < 0) | ~np.isfinite(swe)] = np.nan
    if soil.shape != active.shape or swe.shape != active.shape:
        raise ValueError('Grid and mask shapes disagree')
    return np.where(active[None], np.stack((soil, swe)), np.nan).astype('f4')


def append_buffer(handles, tiles, block, offset):
    """Write each buffered block once, including partial boundary tiles."""
    for ds, tile in zip(handles, tiles, strict=True):
        y0, y1, x0, x1 = tile['window']
        for j, name in enumerate(FIELDS):
            ds[name][offset:offset+len(block)] = np.ma.masked_invalid(block[:, j, y0:y1, x0:x1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', choices=('cnrfc', 'ucrb'), default='cnrfc')
    parser.add_argument('--start', type=date.fromisoformat, default=date(1980, 1, 1))
    parser.add_argument('--end', type=date.fromisoformat, default=date(1980, 12, 31))
    parser.add_argument('--tile-size', type=int, default=128)
    parser.add_argument('--buffer-days', type=int, default=31)
    args = parser.parse_args()
    domain = args.domain
    if args.start > args.end or not 16 <= args.tile_size <= 160 or not 1 <= args.buffer_days <= 31:
        parser.error('Invalid date range, tile size (16–160), or buffer (1–31 days)')
    root = Path(__file__).resolve().parents[1]
    job, user = os.environ['SLURM_JOB_ID'], os.environ['SLURM_JOB_USER']
    work = Path(f'/scratch/{user}/job_{job}/history-stream')
    work.mkdir(parents=True, exist_ok=False)
    state = root / f'nwm/status/{domain}/climatology/history-stream-{job}'
    state.mkdir(parents=True, exist_ok=False)
    output = root / f'nwm/analysis/{domain}/retro/climatology/history-stream-{job}'
    report = {'status': 'running', 'domain': domain, 'job_id': job, 'start': str(args.start), 'end': str(args.end),
              'tile_size': args.tile_size, 'buffer_days': args.buffer_days,
              'source_model_campaign': MODEL_CAMPAIGN,
              'product_status': 'isolated extraction benchmark', 'days_written': 0}
    started = time.monotonic()
    try:
        days = dates(args.start, args.end)
        evidence = []
        for month in sorted({d.strftime('%Y%m') for d in days}):
            evidence.extend(accepted_month(root, month, domain))
        mask_path = root / f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
        mask_identity = identity(mask_path)
        sources = [root / f'nwm/outputs/{domain}/retro/daily' /
                   d.strftime('%Y/%m/%Y%m%d.LDASOUT_DOMAIN1.daily') for d in days]
        records = [{'path': str(p.relative_to(root)), 'identity': identity(p)} for p in sources]
        with Dataset(mask_path) as mask, Dataset(sources[0]) as first:
            active = np.asarray(mask['model_mask'][:], dtype=bool)
            lat, lon = mask['lat'][:], mask['lon'][:]
            crs = {k: first['crs'].getncattr(k) for k in first['crs'].ncattrs() if k != '_FillValue'}
            tiles = []
            all_windows = tile_windows(active.shape, (args.tile_size, args.tile_size))
            for i, w in enumerate(all_windows):
                y0, y1, x0, x1 = w
                yy, xx = slice(y0, y1), slice(x0, x1)
                if not active[yy, xx].any():
                    continue
                tiles.append({'name': f'tile-{i:03d}', 'window': list(w),
                              'active': active[yy, xx].tolist(), 'lat': lat[yy, xx].tolist(),
                              'lon': lon[yy, xx].tolist(), 'x': first['x'][xx].tolist(),
                              'y': first['y'][yy].tolist(), 'crs': crs})
        estimated = len(days)*active.size*2*4
        if shutil.disk_usage(work).free < estimated*1.2 + 2*1024**3:
            raise ValueError('Insufficient scratch for conservatively uncompressed histories')
        report.update(total_tiles=len(all_windows), active_tiles=len(tiles),
                      active_cells=int(active.sum()), sources=len(sources),
                      input_scan_seconds=0., buffered_write_seconds=0.)
        write_json(state / 'plan.json', report | {'tiles': [{'name': t['name'], 'window': t['window']} for t in tiles],
                                                'mask_sha256': digest(mask_path), 'sources': records,
                                                'model_acceptance_pins': evidence})
        times = date2num([datetime.combine(d, datetime.min.time())+timedelta(hours=12) for d in days], UNITS)
        buffer = np.empty((args.buffer_days, 2, *active.shape), dtype='f4')
        paths = []
        with ExitStack() as stack:
            handles = []
            for tile in tiles:
                path = work / (tile['name']+'.nc'); paths.append(path)
                ds = stack.enter_context(Dataset(path, 'w')); handles.append(ds)
                create_grid(ds, tile)
                ds.createDimension('time', len(days)); ds.createDimension('bounds', 2)
                t = ds.createVariable('time', 'f8', ('time',)); t.units = UNITS
                t.calendar = 'proleptic_gregorian'; t.bounds = 'time_bounds'; t[:] = times
                b = ds.createVariable('time_bounds', 'f8', ('time', 'bounds')); b.units = UNITS
                b[:] = np.column_stack((times-.5, times+.5))
                for name, units in zip(FIELDS, ('mm', 'kg m-2'), strict=True):
                    v = ds.createVariable(name, 'f4', ('time', 'y', 'x'), fill_value=-9999.,
                                          zlib=True, complevel=2,
                                          chunksizes=(min(args.buffer_days, len(days)), *np.shape(tile['active'])))
                    v.set_var_chunk_cache(size=4*1024**2, nelems=1009, preemption=.75)
                    v.units = units; v.cell_methods = 'time: mean'
                    v.coordinates = 'lat lon'; v.grid_mapping = 'crs'
                ds.history_start = str(args.start); ds.history_end = str(args.end)
                ds.day_definition = 'model_interval'; ds.soil_thickness_m = THICKNESSES
                ds.product_status = 'isolated extraction benchmark'
                ds.source_model_campaign = MODEL_CAMPAIGN
                ds.domain = domain
            for i, (day, path) in enumerate(zip(days, sources, strict=True)):
                tick = time.monotonic()
                with Dataset(path) as ds:
                    buffer[i % args.buffer_days] = derive(ds, day, active)
                report['input_scan_seconds'] += time.monotonic()-tick
                if identity(path) != records[i]['identity']:
                    raise ValueError(f'Source changed: {path}')
                n = i % args.buffer_days + 1
                if n == args.buffer_days or i == len(days)-1:
                    tick = time.monotonic()
                    append_buffer(handles, tiles, buffer[:n], i+1-n)
                    report['buffered_write_seconds'] += time.monotonic()-tick
                    report['days_written'] = i+1
                    write_json(state / 'status.json', report)
                    print(json.dumps({'days_written': i+1, 'elapsed_seconds': time.monotonic()-started}), flush=True)
        # Independent source comparisons on first/middle/last dates across every tile.
        checked = 0
        for row in sorted({0, len(days)//2, len(days)-1}):
            with Dataset(sources[row]) as ds:
                raw = np.ma.asarray(ds['SOIL_M'][0], dtype='f8').filled(np.nan)
                soil = sum(raw[:, j, :]*float(h)*1000 for j, h in enumerate(THICKNESSES))
                soil[(~np.isfinite(raw) | (raw < 0) | (raw > 1)).any(axis=1)] = np.nan
                swe = np.ma.asarray(ds['SNEQV'][0], dtype='f8').filled(np.nan)
                swe[(swe < 0) | ~np.isfinite(swe)] = np.nan
                expected = np.where(active[None], np.stack((soil, swe)), np.nan).astype('f4')
            for tile, path in zip(tiles, paths, strict=True):
                y0, y1, x0, x1 = tile['window']
                with Dataset(path) as ds:
                    np.testing.assert_array_equal(ds['time'][:], times)
                    for j, name in enumerate(FIELDS):
                        observed = np.ma.asarray(ds[name][row]).filled(np.nan)
                        np.testing.assert_allclose(observed, expected[j, y0:y1, x0:x1],
                                                   rtol=2e-7, atol=1e-5, equal_nan=True)
                        checked += 1
        if identity(mask_path) != mask_identity or any(identity(p) != r['identity']
                                                        for p, r in zip(sources, records, strict=True)):
            raise ValueError('Archive or mask changed during benchmark')
        if any(identity(root/r['path']) != r['identity'] for r in evidence):
            raise ValueError('Model acceptance evidence changed during extraction')
        report['sampled_field_comparisons'] = checked
        report['artifacts'] = [publish(p, output / p.name) for p in paths]
        report.update(status='passed', output_bytes=sum(a['bytes'] for a in report['artifacts']))
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report.update(elapsed_seconds=time.monotonic()-started,
                      max_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
        write_json(state / 'status.json', report)


if __name__ == '__main__':
    main()
