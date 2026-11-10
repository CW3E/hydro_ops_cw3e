"""Candidate-only precipitation summary updates with explicit reuse provenance."""
from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path

import h5py
import numpy as np
from netCDF4 import Dataset, date2num

from .rain_repair_writer import fingerprint
from .temporal_summary import (
    ALGORITHM,
    MONTHLY_ALGORITHM,
    TIME_UNITS,
    midnight,
    migration_stable_identity,
    next_month,
    numeric_time,
)


def identities(paths):
    return [{'path': str(p.resolve()), 'bytes': p.stat().st_size,
             'mtime_ns': p.stat().st_mtime_ns} for p in paths]


def signature(inputs, start, stop, monthly, spec, domain, stream):
    return hashlib.sha256(json.dumps({
        'algorithm': MONTHLY_ALGORITHM if monthly else ALGORITHM,
        'inputs': [migration_stable_identity(i) for i in inputs],
        'start': str(start), 'stop': str(stop), 'from_daily': monthly,
        'spec': spec, 'domain': domain, 'stream': stream,
    }, sort_keys=True).encode()).hexdigest()


def verify_nonrain(original, candidate):
    """Compare every non-rain dataset and its attributes, preserving compressed chunks."""
    with h5py.File(original) as a, h5py.File(candidate) as b:
        if set(a) != set(b):
            raise ValueError('Summary variable set changed')
        for name in a:
            if name == 'RAINRATE':
                continue
            x, y = a[name], b[name]
            if (x.shape, x.dtype, x.chunks) != (y.shape, y.dtype, y.chunks):
                raise ValueError(f'Non-rain encoding changed: {name}')
            # HDF references describe relationships, not meteorological values.
            for key in set(x.attrs) | set(y.attrs):
                if key not in ('DIMENSION_LIST', 'REFERENCE_LIST'):
                    np.testing.assert_array_equal(x.attrs.get(key), y.attrs.get(key))
            if x.chunks:
                if x.id.get_num_chunks() != y.id.get_num_chunks():
                    raise ValueError(f'Non-rain allocation changed: {name}')
                for i in range(x.id.get_num_chunks()):
                    offset = x.id.get_chunk_info(i).chunk_offset
                    if x.id.read_direct_chunk(offset) != y.id.read_direct_chunk(offset):
                        raise ValueError(f'Non-rain chunk changed: {name}')
            elif x.ndim:
                for i in range(x.shape[0]):
                    np.testing.assert_array_equal(x[i], y[i])
            else:
                np.testing.assert_array_equal(x[()], y[()])


def repair_summary(original, destination, inputs, prior_inputs, start, stop, spec,
                   *, monthly=False, block_rows=120):
    """Create isolated candidate; caller must prove each input changed in rain only.

    prior_inputs are pinned original hourly files for daily summaries, or pinned
    original daily summaries for monthly summaries. Their size/mtime must match
    the old summary's signed source inventory. No live publication occurs here.
    """
    original, destination = Path(original), Path(destination)
    inputs, prior_inputs = list(map(Path, inputs)), list(map(Path, prior_inputs))
    if destination.exists() or destination.resolve() == original.resolve():
        raise FileExistsError(destination)
    if block_rows < 1 or spec['reducers'].get('RAINRATE') != 'mean':
        raise ValueError('Requires positive block size and mean RAINRATE')
    if spec['output_names'].get('RAINRATE', 'RAINRATE') != 'RAINRATE':
        raise ValueError('Rain output renaming is not supported')
    if (monthly and start.day != 1) or stop != (next_month(start) if monthly else start + timedelta(days=1)):
        raise ValueError('Expected complete day or calendar month')
    ndays = (stop-start).days
    if len(inputs) != (ndays if monthly else 2) or len(prior_inputs) != len(inputs):
        raise ValueError('Incorrect input coverage')
    pins = {p: fingerprint(p) for p in [original, *inputs, *prior_inputs]}
    t0, t1 = date2num([midnight(start), midnight(stop)], TIME_UNITS)
    current = identities(inputs)
    with Dataset(original) as old:
        domain, stream = old.domain, old.forcing_stream
        old_sources = json.loads(old.source_files)
        if (json.loads(old.reducer_spec) != spec
                or old.aggregation_algorithm != (MONTHLY_ALGORITHM if monthly else ALGORITHM)
                or old.temporal_resolution != ('P1M' if monthly else 'P1D')
                or old.aggregation_sample_count != ndays*24
                or old.day_definition != 'model_interval'
                or old.input_resolution != ('daily' if monthly else 'hourly')):
            raise ValueError('Incompatible old summary schema; full regeneration required')
        if old.aggregation_signature != signature(old_sources, start, stop, monthly, spec, domain, stream):
            raise ValueError('Old summary signature invalid; full regeneration required')
        if len(old_sources) != len(prior_inputs):
            raise ValueError('Old source inventory incomplete')
        for rec, prior in zip(old_sources, prior_inputs, strict=True):
            if (Path(rec['path']).name[:8] != prior.name[:8]
                    or (rec['bytes'], rec['mtime_ns']) != (prior.stat().st_size, prior.stat().st_mtime_ns)):
                raise ValueError('Old summary source identity mismatch; full regeneration required')
        np.testing.assert_array_equal(numeric_time(old['time_bounds']), [[t0, t1]])
        np.testing.assert_array_equal(numeric_time(old['time']), [(t0+t1)/2])
        if old['RAINRATE'].cell_methods != 'time: mean':
            raise ValueError('RAINRATE is not a mean rate')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, destination)
    expected_rain = hashlib.sha256()
    with ExitStack() as stack:
        sources = [stack.enter_context(Dataset(p)) for p in inputs]
        out = stack.enter_context(Dataset(destination, 'r+'))
        ny, nx = len(out.dimensions['y']), len(out.dimensions['x'])
        records = []
        for i, ds in enumerate(sources):
            if ds['RAINRATE'].dimensions != ('time', 'y', 'x') or ds['RAINRATE'].shape[1:] != (ny, nx):
                raise ValueError('Rain grid dimensions mismatch')
            if ds['RAINRATE'].units != out['RAINRATE'].units:
                raise ValueError('Rain units mismatch')
            times = numeric_time(ds['time'])
            if monthly:
                if (ds.aggregation_algorithm != ALGORITHM or json.loads(ds.reducer_spec) != spec
                        or ds.aggregation_sample_count != 24):
                    raise ValueError('Incompatible daily input')
                np.testing.assert_array_equal(numeric_time(ds['time_bounds']),
                                              [[t0+24*i, t0+24*(i+1)]])
                np.testing.assert_array_equal(times, [t0+24*i+12])
                indices = [0]
            else:
                indices = np.flatnonzero((times > t0) & (times <= t1))
            records.extend((ds, int(j), times[j]) for j in indices)
            for name in ('lat', 'lon', 'x', 'y'):
                if name in out.variables:
                    np.testing.assert_array_equal(out[name][:], ds[name][:])
        np.testing.assert_array_equal([r[2] for r in records],
                                      np.arange(t0+12, t1, 24) if monthly else np.arange(t0+1, t1+1))
        for y in range(0, ny, block_rows):
            total = np.zeros((min(block_rows, ny-y), nx), 'f8')
            missing = np.zeros(total.shape, bool)
            for ds, j, _ in records:
                value = np.ma.asarray(ds['RAINRATE'][j, y:y+block_rows, :], dtype='f8')
                missing |= np.ma.getmaskarray(value) | ~np.isfinite(value.filled(np.nan))
                total += value.filled(np.nan)
            # Complete UTC days have identical durations; this is duration-weighted mean.
            result = np.ma.array(total/len(records), mask=missing).astype('f4')
            expected_rain.update(np.ma.getmaskarray(result).tobytes())
            expected_rain.update(result.compressed().tobytes())
            out['RAINRATE'][0, y:y+block_rows, :] = result
        out.source_files = json.dumps(current)
        out.aggregation_signature = signature(current, start, stop, monthly, spec, domain, stream)
        out.rain_summary_repair = 'precipitation_only_v1'
        out.nonrain_summary_reused = 'true'
        out.previous_summary = str(original.resolve())
    actual_rain = hashlib.sha256()
    with Dataset(destination) as check:
        for y in range(0, ny, block_rows):
            result = np.ma.asarray(check['RAINRATE'][0, y:y+block_rows, :], dtype='f4')
            actual_rain.update(np.ma.getmaskarray(result).tobytes())
            actual_rain.update(result.compressed().tobytes())
    if actual_rain.digest() != expected_rain.digest():
        raise ValueError('Rain summary readback mismatch')
    verify_nonrain(original, destination)
    if any(fingerprint(p) != v for p, v in pins.items()):
        raise ValueError('Input changed during summary repair')
    return {'status': 'candidate_verified', 'path': str(destination),
            'nonrain_unchanged': True, 'rain_readback_sha256': actual_rain.hexdigest(),
            'samples': ndays*24}
