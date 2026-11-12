#!/usr/bin/env python3
"""Build an isolated, audited full-domain monthly forcing percentile table."""
import argparse
import json
import os
import time
from pathlib import Path

import netCDF4
import numpy as np

from hydro_ops.forcing.monthly_climatology import LEVELS, METHOD, quantiles


def identity(path):
    s = path.stat()
    return [s.st_ino, s.st_size, s.st_mtime_ns]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--domain', choices=['cnrfc', 'ucrb'], required=True)
    p.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--min-samples', type=int, default=30)
    args = p.parse_args()
    started = time.monotonic()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'domain': args.domain, 'reference_start': '1979-10-01',
                  'reference_end': '2025-09-30', 'method': METHOD, 'min_samples': args.min_samples,
                  'sources': [], 'months': []}
    destination = args.output_dir / 'monthly_forcing_percentiles.nc'
    temporary = destination.with_suffix('.nc.partial')
    try:
        root = args.project_root / 'forcing/outputs' / args.domain / 'retro/monthly'
        sources = []
        for year in range(1979, 2026):
            for month in range(1, 13):
                key = year * 100 + month
                if not 197910 <= key <= 202509:
                    continue
                path = root / str(year) / f'{key}.LDASIN_DOMAIN1.monthly'
                receipt = Path(str(path) + '.subset.json')
                r = json.loads(receipt.read_text())
                ident = identity(path)
                if r['status'] != 'passed' or r['output_identity'] != ident:
                    raise ValueError(f'Stale or unaccepted subset: {path}')
                row = {'path': str(path), 'identity': ident, 'receipt': str(receipt),
                           'receipt_identity': identity(receipt), 'year': year, 'month': month}
                sources.append(row)
        report['sources'] = sources
        with netCDF4.Dataset(sources[0]['path']) as first, netCDF4.Dataset(temporary, 'w') as out:
            shape = first['T2D'].shape[1:]
            out.createDimension('month', 12)
            out.createDimension('percentile', len(LEVELS))
            for name in ('y', 'x', 'nv4'):
                out.createDimension(name, len(first.dimensions[name]))
            out.createVariable('month', 'i2', ('month',))[:] = np.arange(1, 13)
            out.createVariable('percentile', 'f4', ('percentile',))[:] = LEVELS
            out['percentile'].units = 'percent'
            static = {}
            for name in ('lat', 'lon', 'lat_bnds', 'lon_bnds', 'subset_model_mask', 'subset_forcing_mask'):
                v = first[name]
                static[name] = v[:]
                w = out.createVariable(name, v.dtype, v.dimensions, zlib=True, complevel=2)
                w.setncatts({k: v.getncattr(k) for k in v.ncattrs() if k != '_FillValue'})
                w[:] = static[name]
            units = {name: first[name].units for name in ('T2D', 'RAINRATE')}
            for name, unit in units.items():
                v = out.createVariable(name, 'f4', ('month', 'percentile', 'y', 'x'),
                                       fill_value=-9999., zlib=True, complevel=2,
                                       chunksizes=(1, 1, min(128, shape[0]), min(128, shape[1])))
                v.units = unit
                v.long_name = f'Calendar-month percentile of monthly mean {name}'
                v.coordinates = 'lat lon'
                out.createVariable(name + '_sample_count', 'i2', ('month', 'y', 'x'),
                                   zlib=True, complevel=2)
            out.setncatts({'reference_start': '1979-10-01', 'reference_end': '2025-09-30',
                              'quantile_method': METHOD, 'minimum_samples': args.min_samples,
                              'domain': args.domain, 'source_stream': 'retro',
                              'precipitation_statistic': 'monthly mean rate, not accumulated depth',
                              'temperature_statistic': 'monthly mean T2D, not midpoint or extrema',
                              'missing_policy': 'exclude nonfinite samples; preserve source forcing coverage'})
            for month in range(1, 13):
                tick = time.monotonic()
                rows = [r for r in sources if r['month'] == month]
                if len(rows) != 46:
                    raise ValueError('Expected 46 samples per calendar month')
                for name, unit in units.items():
                    data = np.empty((46, *shape), dtype='f4')
                    for i, row in enumerate(rows):
                        with netCDF4.Dataset(row['path']) as ds:
                            v = ds[name]
                            if v.shape != (1, *shape) or v.units != unit:
                                raise ValueError(f'Grid/units mismatch: {row["path"]}')
                            for coord, reference in static.items():
                                if not np.ma.allequal(ds[coord][:], reference):
                                    raise ValueError(f'Changed grid/mask: {row["path"]}')
                            t = ds['time']
                            bounds = ds[t.bounds]
                            expected = [(row['year'], month, 1, 0, 0, 0),
                                        (row['year'] + (month == 12), month % 12 + 1, 1, 0, 0, 0)]
                            actual = netCDF4.num2date(bounds[:].reshape(-1), bounds.units,
                                                     getattr(bounds, 'calendar', 'standard'))
                            if [(d.year, d.month, d.day, d.hour, d.minute, d.second) for d in actual] != expected:
                                raise ValueError(f'Incorrect monthly bounds: {row["path"]}')
                            data[i] = np.ma.asarray(v[0], dtype='f4').filled(np.nan)
                    q, count = quantiles(data, args.min_samples)
                    if np.any(np.diff(q, axis=0) < 0):
                        raise ValueError('Nonmonotonic quantiles')
                    if name == 'RAINRATE' and np.any(data[np.isfinite(data)] < 0):
                        raise ValueError('Negative precipitation')
                    # Independent cell-wise oracle, deterministic coverage across the domain.
                    eligible = np.flatnonzero(count.ravel() >= args.min_samples)
                    for cell in eligible[::max(1, len(eligible) // 32)]:
                        expected = np.nanpercentile(data.reshape(46, -1)[:, cell], LEVELS,
                                                    method=METHOD).astype('f4')
                        np.testing.assert_allclose(q.reshape(100, -1)[:, cell], expected,
                                                   rtol=1e-6, atol=0)
                    out[name][month-1] = np.ma.masked_invalid(q)
                    out[name + '_sample_count'][month-1] = count
                    out.sync()
                    np.testing.assert_equal(out[name][month-1].filled(np.nan), q)
                    del data, q
                report['months'].append({'month': month, 'seconds': time.monotonic()-tick})
                print(json.dumps(report['months'][-1]), flush=True)
        for row in sources:
            if identity(Path(row['path'])) != row['identity'] or identity(Path(row['receipt'])) != row['receipt_identity']:
                raise ValueError('Source changed during processing')
        os.replace(temporary, destination)
        report.update(status='passed', output=str(destination), output_identity=identity(destination))
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        (args.output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
