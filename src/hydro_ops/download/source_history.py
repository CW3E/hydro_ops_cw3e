"""Compact before/after evidence for replacement of normalized source grids.

Hashes of decoded values deliberately ignore NetCDF compression and global
history attributes. They retain dimensions, coordinates, units and missingness.
Callers must serialize updates for a source; prepared records are not proof of
successful publication. This stores evidence, not recoverable old grid values.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import xarray as xr


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + '.part')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temporary.replace(path)


def array_hash(values: np.ndarray) -> str:
    array = np.asarray(values, dtype='<f8').copy()
    array[np.isnan(array)] = np.nan
    array[array == 0] = 0  # Canonicalize negative zero.
    return hashlib.sha256(array.tobytes(order='C')).hexdigest()


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def inspect_source(path: Path, variable: str) -> tuple[dict, np.ndarray]:
    checksum = file_hash(path)
    with xr.open_dataset(path) as ds:
        field = ds[variable]
        values = np.asarray(field.values, dtype='f8')
        grid = {'dimensions': list(field.dims), 'shape': list(values.shape),
                'coordinates': {name: {'shape': list(ds[name].shape),
                                       'hash': array_hash(ds[name].values),
                                       'units': ds[name].attrs.get('units')}
                                for name in ('lat', 'lon') if name in ds}}
        units = field.attrs.get('units')
    return {'file_sha256': checksum, 'values_sha256': array_hash(values),
            'grid': grid, 'units': units, 'finite_cells': int(np.isfinite(values).sum())}, values


def replacement_evidence(old: Path, new: Path, variable: str) -> dict:
    after, new_values = inspect_source(new, variable)
    result = {'version': 1, 'variable': variable, 'new': after, 'old': None,
              'comparison': {'kind': 'initial_download'}}
    if not old.exists():
        return result
    try:
        before, old_values = inspect_source(old, variable)
    except (OSError, ValueError, KeyError) as error:
        # An invalid old grid is a reason to redownload, not to block recovery.
        # Retain byte-level evidence but do not invent a decoded-value comparison.
        # If the old bytes themselves cannot be read, file_hash still raises.
        result['old'] = {'file_sha256': file_hash(old),
                         'inspection_error': f'{type(error).__name__}: {error}'}
        result['comparison'] = {'kind': 'old_grid_unreadable', 'values_changed': None}
        return result
    result['old'] = before
    if before['grid'] != after['grid'] or before['units'] != after['units']:
        result['comparison'] = {'kind': 'grid_or_units_changed', 'values_changed': None}
        return result
    a, b = np.isfinite(old_values), np.isfinite(new_values)
    common = a & b
    delta = new_values[common] - old_values[common]
    changed = int(np.count_nonzero(delta))
    result['comparison'] = {
        'kind': 'comparable',
        'values_changed': before['values_sha256'] != after['values_sha256'],
        'common_finite_cells': int(common.sum()), 'changed_finite_cells': changed,
        'newly_finite_cells': int((~a & b).sum()), 'newly_missing_cells': int((a & ~b).sum()),
        'mean_change': float(delta.mean()) if delta.size else None,
        'rms_change': float(np.sqrt(np.mean(delta * delta))) if delta.size else None,
        'max_abs_change': float(np.max(np.abs(delta))) if delta.size else None,
        'units': after['units'],
    }
    return result
