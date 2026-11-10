"""Assembly adapter enforcing the static rain envelope without refitting PRISM.

The pinned scientific-window and copy/replace writers remain unchanged. Only
scratch copies of accepted windows are masked; retained rainfall is untouched.
"""
import shutil
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.rain_repair_writer import fingerprint, write_day


def mask_values(values, active, keep):
    result = np.ma.asarray(values, dtype='f8').filled(np.nan)
    if result.shape != keep.shape or active.shape != keep.shape or np.any(active & ~keep):
        raise ValueError('Inconsistent active/static envelope masks')
    if not np.isfinite(result[active]).all() or np.any(result[keep & np.isfinite(result)] < 0):
        raise ValueError('Invalid retained rainfall; masking cannot repair active gaps or negatives')
    removed = int(np.isfinite(result[~keep]).sum())
    result[~keep] = np.nan
    return np.ma.masked_invalid(result), removed


def write_enveloped_day(original, windows, indices, destination, work, day, active, keep, metadata):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=False)
    pins = {Path(p): fingerprint(p) for p in set(windows)}
    projected = {}
    counts = {}
    for number, path in enumerate(pins):
        temporary = work/f'window-{number}.nc'
        shutil.copy2(path, temporary)
        with Dataset(path) as source, Dataset(temporary, 'r+') as ds:
            if str(getattr(source, 'prism_reconciliation_accepted', '')).lower() != 'true':
                raise ValueError('Unaccepted input PRISM window')
            removed = 0
            for hour in range(len(ds.dimensions['time'])):
                expected, n = mask_values(source['RAINRATE'][hour], active, keep)
                removed += n
                ds['RAINRATE'][hour] = expected
                timing = np.asarray(source['prism_timing_source_id'][hour]).copy()
                timing[~keep] = 0
                ds['prism_timing_source_id'][hour] = timing
            counts[str(path)] = removed
        # Check decoded on-disk rain against original accepted values inside keep.
        with Dataset(path) as source, Dataset(temporary) as ds:
            for hour in range(len(ds.dimensions['time'])):
                expected, _ = mask_values(source['RAINRATE'][hour], active, keep)
                actual = ds['RAINRATE'][hour]
                np.testing.assert_array_equal(np.ma.getmaskarray(actual), np.ma.getmaskarray(expected))
                np.testing.assert_array_equal(actual.compressed(), expected.compressed())
        projected[path] = temporary
    attributes = dict(metadata, rain_envelope_assembly='static_keep_v4_retained_rain_unchanged')
    row = write_day(original, [projected[Path(p)] for p in windows], indices,
                    destination, work/'writer', day, active, keep, attributes)
    if any(fingerprint(p) != v for p, v in pins.items()):
        raise ValueError('Accepted window changed during envelope assembly')
    row['envelope_assembly'] = {'retained_window_rain_exact': True,
                                'accepted_window_identities': {str(p): list(v) for p, v in pins.items()},
                                'removed_window_cell_hours': counts,
                                'original_windows_modified': False}
    for path in projected.values():
        path.unlink()
    return row
