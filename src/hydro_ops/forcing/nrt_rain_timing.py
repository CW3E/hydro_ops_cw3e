"""NLDAS timing from hourly or daily storage; missing hours are never dry."""
from datetime import UTC, datetime

import numpy as np
from netCDF4 import Dataset, num2date
from scipy.spatial import cKDTree

from hydro_ops.forcing.source_selection import source_paths


def resolve_timing_paths(paths, stamps):
    resolved = []
    times = {}
    for daily, stamp in zip(paths, stamps, strict=True):
        valid = datetime(stamp.year, stamp.month, stamp.day, stamp.hour, tzinfo=UTC)
        selected = daily
        for candidate in source_paths('nldas2', daily.parent.parent, valid):
            if not candidate.is_file():
                continue
            if candidate not in times:
                with Dataset(candidate) as ds:
                    times[candidate] = list(num2date(ds['time'][:], ds['time'].units))
            if stamp in times[candidate]:
                selected = candidate
                break
        resolved.append(selected)
    return resolved


def load_optional_timing(paths, stamps, lon, lat):
    paths = resolve_timing_paths(paths, stamps)
    result = np.full((len(stamps), len(lon)), np.nan, dtype='f8')
    report = {'used_files': [], 'missing_files': [], 'missing_hours': []}
    for path in dict.fromkeys(paths):
        try:
            ds = Dataset(path)
        except FileNotFoundError:
            report['missing_files'].append(str(path))
            continue
        # Malformed files/units and other I/O errors must still fail closed.
        with ds:
            if ds['Rainf'].units != 'kg m-2':
                raise ValueError('Unexpected NLDAS accumulation units')
            ts = list(num2date(ds['time'][:], ds['time'].units))
            gx, gy = np.meshgrid(ds['lon'][:], ds['lat'][:])
            tree = cKDTree(np.column_stack((gx.ravel(), gy.ravel())))
            _, donor = tree.query(np.column_stack(((lon+180) % 360-180, lat)))
            for i, requested in enumerate(paths):
                if requested != path:
                    continue
                if stamps[i] not in ts:
                    report['missing_hours'].append(str(stamps[i]))
                    continue
                values = np.ma.asarray(ds['Rainf'][ts.index(stamps[i])],
                                       dtype='f8').filled(np.nan).reshape(-1)
                result[i] = values[donor]
            report['used_files'].append(str(path))
    return result, report
