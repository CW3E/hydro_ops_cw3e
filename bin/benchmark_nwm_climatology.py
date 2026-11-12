"""SLURM benchmark of full-reference CNRFC soil-water/SWE climatology tiles."""
# Model daily clocks use timezone-naive UTC.
import argparse
import hashlib
import json
import multiprocessing
import os
import re
import resource
import shutil
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, date2num, num2date

from hydro_ops.wrf_hydro.climatology import (
    METHOD,
    PERCENTILES,
    REFERENCE_END,
    REFERENCE_START,
    THICKNESSES,
    assemble_tiles,
    calendar_windows,
    column_soil_water,
    dates,
    percentile_rank,
    statistics,
    tile_windows,
)
from hydro_ops.wrf_hydro.production import write_json

FIELDS = ('SOIL_WATER', 'SNEQV')
TIME_UNITS = 'days since 1970-01-01 00:00:00'


def identity(path):
    s = path.stat()
    return [s.st_ino, s.st_size, s.st_mtime_ns]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def extract_year(task):
    root, domain, year, day_strings, tiles, work = task
    root, work = Path(root), Path(work)
    started = time.monotonic()
    # Verify the documented layer thicknesses against each monthly run namelist.
    campaign = 'repaired_rain_retro_v1' if domain in ('cnrfc', 'ucrb') else 'production_v1'
    for month in sorted({d[:7].replace('-', '') for d in day_strings}):
        folder = root / f'nwm/runs/{domain}/retro/{campaign}' / month
        acceptance = json.loads((folder / 'accepted.json').read_text())
        if not acceptance.get('passed') or acceptance.get('stream') != 'retro':
            raise ValueError(f'Unaccepted source month: {folder}')
        text = (folder / 'namelist.hrldas').read_text()
        layers = re.findall(r'soil_thick_input\(\s*(\d+)\s*\)\s*=\s*([\d.eE+-]+)', text, re.IGNORECASE)
        actual = [float(v) for _, v in sorted(layers, key=lambda v: int(v[0]))]
        if len(actual) != 4 or not np.allclose(actual, THICKNESSES, rtol=0, atol=1e-12):
            raise ValueError(f'Unexpected soil thicknesses: {folder}')
    size = tiles[0]['window'][1] - tiles[0]['window'][0]
    values = np.full((len(day_strings), len(tiles), 2, size, size), np.nan, dtype='f4')
    records = []
    for row, stamp in enumerate(day_strings):
        day = date.fromisoformat(stamp)
        source = root / f'nwm/outputs/{domain}/retro/daily' / day.strftime('%Y/%m/%Y%m%d.LDASOUT_DOMAIN1.daily')
        before = identity(source)
        with Dataset(source) as ds:
            if len(ds.dimensions['time']) != 1 or getattr(ds, 'day_definition', '') != 'model_interval':
                raise ValueError(f'Invalid daily collection: {source}')
            bounds = ds['time_bounds']
            endpoints = num2date(bounds[:].reshape(-1), bounds.units)
            expected = [datetime.combine(day, datetime.min.time()),
                        datetime.combine(day+timedelta(days=1), datetime.min.time())]
            if [t.isoformat() for t in endpoints] != [t.isoformat() for t in expected]:
                raise ValueError(f'Wrong model-day bounds: {source}')
            for name, units in [('SOIL_M', 'm3 m-3'), ('SNEQV', 'kg m-2')]:
                if ds[name].units != units or ds[name].cell_methods != 'time: mean':
                    raise ValueError(f'Unexpected field semantics: {source} {name}')
            if ds['SOIL_M'].dimensions != ('time', 'y', 'soil_layers_stag', 'x'):
                raise ValueError('Unexpected soil dimension order')
            for k, tile in enumerate(tiles):
                y0, y1, x0, x1 = tile['window']
                active = np.asarray(tile['active'], dtype=bool)
                soil = column_soil_water(ds['SOIL_M'][0, y0:y1, :, x0:x1])
                swe = np.ma.asarray(ds['SNEQV'][0, y0:y1, x0:x1], dtype='f8').filled(np.nan)
                swe[(swe < 0) | ~np.isfinite(swe)] = np.nan
                values[row, k, 0] = np.where(active, soil, np.nan)
                values[row, k, 1] = np.where(active, swe, np.nan)
        if identity(source) != before:
            raise ValueError(f'Source changed during extraction: {source}')
        records.append({'path': str(source.relative_to(root)), 'identity': before})
    np.save(work / f'{year}.npy', values)
    report = {'year': year, 'days': len(day_strings), 'sources': records,
              'elapsed_seconds': time.monotonic()-started,
              'max_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    write_json(work / f'{year}.json', report)
    return {k: v for k, v in report.items() if k != 'sources'}


def create_grid(ds, tile):
    ny, nx = np.shape(tile['active'])
    ds.createDimension('y', ny); ds.createDimension('x', nx)
    for name, dim in [('x', 'x'), ('y', 'y')]:
        v = ds.createVariable(name, 'f8', (dim,)); v[:] = tile[name]
        v.units = 'm'; v.axis = name.upper()
        v.standard_name = 'projection_' + name + '_coordinate'
    for name, unit in [('lat', 'degrees_north'), ('lon', 'degrees_east')]:
        v = ds.createVariable(name, 'f8', ('y', 'x')); v[:] = tile[name]; v.units = unit
        v.standard_name = 'latitude' if name == 'lat' else 'longitude'
    crs = ds.createVariable('crs', 'i4'); crs.setncatts(tile['crs']); crs.assignValue(0)
    ds.tile_window = json.dumps(tile['window'])


def write_history(path, tile, times, values):
    with Dataset(path, 'w') as ds:
        create_grid(ds, tile)
        ds.createDimension('time', len(times)); ds.createDimension('bounds', 2)
        t = ds.createVariable('time', 'f8', ('time',)); t.units = TIME_UNITS
        t.calendar = 'proleptic_gregorian'; t.bounds = 'time_bounds'
        t[:] = date2num([datetime.combine(d, datetime.min.time()) + timedelta(hours=12) for d in times], TIME_UNITS)
        b = ds.createVariable('time_bounds', 'f8', ('time', 'bounds')); b.units = TIME_UNITS
        b[:] = np.column_stack((t[:]-0.5, t[:]+0.5))
        for i, name in enumerate(FIELDS):
            v = ds.createVariable(name, 'f4', ('time', 'y', 'x'), fill_value=-9999.,
                                  zlib=True, complevel=2, chunksizes=(min(31, len(times)), *values.shape[-2:]))
            v[:] = np.ma.masked_invalid(values[:, i]); v.units = 'mm' if i == 0 else 'kg m-2'
            v.cell_methods = 'time: mean'; v.coordinates = 'lat lon'; v.grid_mapping = 'crs'
        ds.reference_start = str(REFERENCE_START); ds.reference_end = str(REFERENCE_END)


def write_table(path, tile, results, counts, windows):
    with Dataset(path, 'w') as ds:
        create_grid(ds, tile)
        ds.createDimension('calendar_day', 366); ds.createDimension('percentile', len(PERCENTILES))
        d = ds.createVariable('calendar_day', 'i2', ('calendar_day',)); d[:] = np.arange(1, 367)
        d.long_name = 'Day in a leap-year month/day template (2000); not raw Gregorian day-of-year'
        md = ds.createVariable('month_day', 'i2', ('calendar_day',))
        md[:] = [100*x.month+x.day for x in dates(date(2000, 1, 1), date(2000, 12, 31))]
        p = ds.createVariable('percentile', 'f4', ('percentile',)); p[:] = PERCENTILES; p.units = '%'
        n = ds.createVariable('expected_sample_count', 'i4', ('calendar_day',))
        n[:] = [-1 if i == 59 else len(w) for i, w in enumerate(windows)]
        for name in FIELDS:
            v = ds.createVariable(name, 'f4', ('calendar_day', 'percentile', 'y', 'x'),
                                  fill_value=-9999., zlib=True, complevel=2,
                                  chunksizes=(1, len(PERCENTILES), *np.shape(tile['active'])))
            v[:] = np.ma.masked_invalid(results[name]); v.units = 'mm' if name == 'SOIL_WATER' else 'kg m-2'
            v.coordinates = 'lat lon'; v.grid_mapping = 'crs'
            v.long_name = 'Seasonal quantile of daily-mean ' + name
            v.quantile_method = METHOD
            c = ds.createVariable(name+'_sample_count', 'i4', ('calendar_day', 'y', 'x'), zlib=True)
            c[:] = counts[name]; c.comment = '-1 on February 29: interpolated thresholds, no empirical sample count'
        ds.reference_start = str(REFERENCE_START); ds.reference_end = str(REFERENCE_END)
        ds.window_days = 31; ds.percentile_method = 'Hyndman-Fan type 8 (median_unbiased)'
        ds.leap_day_policy = 'Feb29 thresholds average Feb28/Mar01; leap observations remain in neighboring windows'
        ds.soil_thickness_m = THICKNESSES
        ds.swe_rank_minimum_mm = 1.; ds.swe_reference_policy = 'Retain zeros; threshold applies only to rank maps'
        ds.product_status = 'benchmark tile, not a complete domain climatology'


def direct_oracle(values, windows, quantiles):
    """Independent sorted-sample implementation of type-8 interpolation."""
    tested = 0
    for day in (0, 58, 60, 181, 273, 365):
        for pixel in np.linspace(0, values.shape[1]*values.shape[2]-1, 9, dtype=int):
            a = values[windows[day]].reshape(len(windows[day]), -1)[:, pixel]
            a = np.sort(a[np.isfinite(a)].astype('f8'))
            if not len(a):
                continue
            h = (len(a)+1/3) * PERCENTILES / 100 - 2/3
            expected = np.interp(h, np.arange(len(a)), a)
            np.testing.assert_allclose(quantiles[day].reshape(100, -1)[:, pixel], expected,
                                       rtol=2e-7, atol=1e-5)
            tested += 1
    if not tested:
        raise ValueError('No valid direct-reference samples')
    return tested


def cdo_checks(history, work, quantiles, start=REFERENCE_START, end=REFERENCE_END):
    """Round-trip projected tile and compare one active pixel with CDO p50."""
    log = work / 'cdo.log'
    def call(args):
        with log.open('a') as f:
            subprocess.run(['cdo', '-O', *map(str, args)], check=True, stdout=f, stderr=f,
                           env=os.environ | {'CDO_PCTL_NBINS': '10001'})
    call(['seltimestep,1', history, work / 'day.nc'])
    call(['distgrid,2,2', work / 'day.nc', work / 'part'])
    parts = sorted(work.glob('part*.nc'))
    call(['collgrid,2', *parts, work / 'reassembled.nc'])
    with Dataset(work / 'day.nc') as a, Dataset(work / 'reassembled.nc') as b:
        for name in (*FIELDS, 'lat', 'lon'):
            np.testing.assert_array_equal(np.ma.getmaskarray(a[name][:]), np.ma.getmaskarray(b[name][:]))
            np.testing.assert_array_equal(np.ma.asarray(a[name][:]).compressed(), np.ma.asarray(b[name][:]).compressed())
    # Independent indexed split/reassembly, including the whole percentile dimension.
    a = quantiles['SOIL_WATER'][:2]
    pieces = [(w, a[..., w[0]:w[1], w[2]:w[3]]) for w in tile_windows(a.shape[-2:], (7, 9))]
    np.testing.assert_array_equal(assemble_tiles(pieces, a.shape[-2:]), a)
    with Dataset(history) as ds:
        sample = np.ma.asarray(ds['SOIL_WATER'][0]).filled(np.nan)
        y, x = np.argwhere(np.isfinite(sample))[0]
    call([f'selindexbox,{x+1},{x+1},{y+1},{y+1}', history, work / 'pixel.nc'])
    call(['ydrunmin,31', work / 'pixel.nc', work / 'min.nc'])
    call(['ydrunmax,31', work / 'pixel.nc', work / 'max.nc'])
    call(['ydrunpctl,50,31,pm=r8', work / 'pixel.nc', work / 'min.nc', work / 'max.nc', work / 'p50.nc'])
    differences = {}
    # Compute p50 directly: the published table has 49.5 and 50.5, not p50.
    with Dataset(history) as ds, Dataset(work / 'p50.nc') as other:
        time_values = num2date(other['time'][:], other['time'].units)
        all_dates = [date(v.year, v.month, v.day) for v in num2date(ds['time'][:], ds['time'].units)]
        windows = calendar_windows(all_dates, start, end)
        for name in FIELDS:
            values = np.ma.asarray(ds[name][:, y:y+1, x:x+1]).filled(np.nan)
            exact, _ = statistics(values, windows, percentiles=[50.])
            errors = []
            for i, stamp in enumerate(time_values):
                if (stamp.month, stamp.day) == (2, 29):
                    continue
                day = (date(2000, stamp.month, stamp.day)-date(2000, 1, 1)).days
                observed = np.ma.asarray(other[name][i]).filled(np.nan).item()
                sample = values[windows[day], 0, 0]
                tolerance = 2 * (np.nanmax(sample)-np.nanmin(sample)) / 10001 + 1e-4
                if not np.isfinite(observed) or abs(float(exact[day, 0, 0, 0])-observed) > tolerance:
                    raise ValueError(f'CDO p50 differs beyond histogram tolerance: {name} {stamp.month}-{stamp.day}')
                errors.append(abs(float(exact[day, 0, 0, 0])-observed))
            differences[name] = float(np.nanmax(errors))
    return {'projected_distgrid_collgrid_roundtrip': 'passed', 'indexed_roundtrip': 'passed',
            'cdo_p50_max_abs_difference': differences, 'histogram_bins': 10001}


def publish(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.partial')
    shutil.copy2(source, temporary)
    checksum = digest(source)
    if digest(temporary) != checksum:
        raise ValueError('Copy checksum mismatch')
    temporary.replace(destination)
    return {'path': str(destination), 'sha256': checksum, 'bytes': destination.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--tile-size', type=int, default=16)
    parser.add_argument('--preflight-only', action='store_true',
                        help='Synthetic full-calendar CDO/oracle test on the actual projected tile grid')
    args = parser.parse_args()
    if not 1 <= args.workers <= 16 or not 4 <= args.tile_size <= 160:
        parser.error('Use 1–16 workers and 4–160 cells per tile side')
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']; user = os.environ['SLURM_JOB_USER']
    work = Path(f'/scratch/{user}/job_{job}/climatology')
    work.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(work).free < (60 if args.tile_size > 64 else 20) * 1024**3:
        raise RuntimeError('Insufficient scratch')
    state = root / f'nwm/status/cnrfc/climatology/benchmark-{job}'
    state.mkdir(parents=True, exist_ok=False)
    output = root / f'nwm/analysis/cnrfc/retro/climatology/wy1980-2025/benchmark-{job}'
    report = {'status': 'running', 'job_id': job, 'domain': 'cnrfc', 'reference_start': str(REFERENCE_START),
              'reference_end': str(REFERENCE_END), 'workers': args.workers, 'tile_size': args.tile_size,
              'product_status': 'benchmark only', 'phases': {}, 'artifacts': []}
    report['preflight_only'] = args.preflight_only
    started = time.monotonic()
    write_json(state / 'status.json', report)
    try:
        all_dates = dates(REFERENCE_START-timedelta(days=15), REFERENCE_END+timedelta(days=15))
        windows = calendar_windows(all_dates)
        assert all(len(w) == 46*31 for i, w in enumerate(windows) if i != 59)
        mask_path = root / 'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc'
        mask_identity = identity(mask_path)
        tiles = []
        first = root / 'nwm/outputs/cnrfc/retro/daily' / all_dates[0].strftime('%Y/%m/%Y%m%d.LDASOUT_DOMAIN1.daily')
        with Dataset(mask_path) as mask, Dataset(first) as ds:
            active = np.asarray(mask['model_mask'][:], dtype=bool)
            lat, lon = mask['lat'][:], mask['lon'][:]
            crs = {k: ds['crs'].getncattr(k) for k in ds['crs'].ncattrs() if k != '_FillValue'}
            for label, a, b in [('sierra', 37.0, -119.2), ('coast', 40.5, -124.1), ('desert', 34.0, -116.5)]:
                distance = (lat-a)**2 + ((lon-b)*np.cos(np.deg2rad(a)))**2
                y, x = np.unravel_index(np.argmin(np.where(active, distance, np.inf)), active.shape)
                n = args.tile_size
                y0, x0 = max(0, min(int(y)-n//2, active.shape[0]-n)), max(0, min(int(x)-n//2, active.shape[1]-n))
                yy, xx = slice(y0, y0+n), slice(x0, x0+n)
                tiles.append({'name': label, 'window': [y0, y0+n, x0, x0+n], 'active': active[yy, xx].tolist(),
                              'lat': lat[yy, xx].tolist(), 'lon': lon[yy, xx].tolist(),
                              'x': ds['x'][xx].tolist(), 'y': ds['y'][yy].tolist(), 'crs': crs})
        write_json(state / 'plan.json', report | {'tiles': tiles, 'input_start': str(all_dates[0]),
                   'input_end': str(all_dates[-1]), 'mask_sha256': digest(mask_path),
                   'percentiles': PERCENTILES.tolist(), 'method': METHOD})
        if args.preflight_only:
            start, end = date(1980, 1, 1), date(1982, 12, 31)
            synthetic_dates = dates(start-timedelta(days=15), end+timedelta(days=15))
            synthetic_windows = calendar_windows(synthetic_dates, start, end)
            tile = tiles[0]
            rng = np.random.default_rng(84)
            synthetic = rng.uniform(0, 500, (len(synthetic_dates), 2, args.tile_size, args.tile_size)).astype('f4')
            synthetic[:, 1] = np.where(synthetic[:, 1] < 300, 0, synthetic[:, 1]-300)
            synthetic[:, :, ~np.asarray(tile['active'], dtype=bool)] = np.nan
            history_file = work / 'synthetic.nc'
            write_history(history_file, tile, synthetic_dates, synthetic)
            q = {name: statistics(synthetic[:, i], synthetic_windows)[0] for i, name in enumerate(FIELDS)}
            for i, name in enumerate(FIELDS):
                direct_oracle(synthetic[:, i], synthetic_windows, q[name])
            report['checks'] = cdo_checks(history_file, work, q, start, end)
            report['artifacts'].append(publish(work / 'cdo.log', output / 'cdo.log'))
            report.update(status='passed', elapsed_seconds=time.monotonic()-started)
            return
        tasks = [(str(root), 'cnrfc', year, [str(d) for d in all_dates if d.year == year], tiles, str(work))
                 for year in sorted({d.year for d in all_dates})]
        extraction = time.monotonic()
        report['years'] = []
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            for result in pool.map(extract_year, tasks):
                report['years'].append(result)
                write_json(state / 'status.json', report)
                print(json.dumps(result), flush=True)
        report['phases']['extraction_seconds'] = time.monotonic()-extraction
        # Recheck all source identities before accepting the frozen histories.
        for task in tasks:
            year = task[2]
            records = json.loads((work / f'{year}.json').read_text())
            for item in records['sources']:
                if identity(root / item['path']) != item['identity']:
                    raise ValueError('Source archive revised during benchmark')
            shutil.copy2(work / f'{year}.json', state / f'sources-{year}.json')
        if identity(mask_path) != mask_identity:
            raise ValueError('Domain mask changed')
        report['tiles'] = []
        for k, tile in enumerate(tiles):
            tile_start = time.monotonic()
            # Materialize one spatial tile at a time, not all three histories.
            # Memory-map annual intermediates to avoid a second full-size copy.
            history = np.concatenate([
                np.load(work / f'{t[2]}.npy', mmap_mode='r')[:, k] for t in tasks
            ], axis=0)
            folder = work / tile['name']; folder.mkdir()
            history_file = folder / 'history.nc'
            write_history(history_file, tile, all_dates, history)
            history_seconds = time.monotonic()-tile_start
            calculation_start = time.monotonic()
            results, counts, comparisons = {}, {}, {}
            for i, name in enumerate(FIELDS):
                values = history[:, i]
                results[name], counts[name] = statistics(values, windows)
                comparisons[name] = direct_oracle(values, windows, results[name])
                finite = results[name][np.isfinite(results[name])]
                if not len(finite) or np.any(np.diff(results[name], axis=1) < -1e-6):
                    raise ValueError('Empty/nonmonotonic percentile table')
            calculation_seconds = time.monotonic()-calculation_start
            table = folder / 'percentiles.nc'
            write_table(table, tile, results, counts, windows)
            # Example rank maps for a reference day; zeros excluded only at rank time.
            ranks = {}
            sample_date = date(2025, 4, 1)
            row = all_dates.index(sample_date)
            doy = (date(2000, sample_date.month, sample_date.day)-date(2000, 1, 1)).days
            for i, name in enumerate(FIELDS):
                ranks[name] = percentile_rank(history[row, i], results[name][doy],
                                               swe_minimum=1. if name == 'SNEQV' else None)
            np.savez(folder / 'rank-example.npz', **ranks)
            checks = cdo_checks(history_file, folder, results)
            tile_report = {'name': tile['name'], 'window': tile['window'], 'active_cells': int(np.sum(tile['active'])),
                           'elapsed_seconds': time.monotonic()-tile_start, 'direct_oracle_comparisons': comparisons,
                           'history_assembly_write_seconds': history_seconds,
                           'quantiles_and_oracle_seconds': calculation_seconds,
                           **checks}
            report['tiles'].append(tile_report)
            for source in [history_file, table, folder / 'rank-example.npz', folder / 'cdo.log']:
                report['artifacts'].append(publish(source, output / tile['name'] / source.name))
            write_json(state / 'status.json', report)
            print(json.dumps(tile_report), flush=True)
            del history, results, counts, values
        report.update(status='passed', elapsed_seconds=time.monotonic()-started,
                      max_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except BaseException as error:
        report.update(status='failed', error=str(error), elapsed_seconds=time.monotonic()-started)
        raise
    finally:
        write_json(state / 'status.json', report)


if __name__ == '__main__':
    main()
