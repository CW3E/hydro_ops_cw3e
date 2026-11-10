"""Candidate-only copy-and-replace precipitation writer; no scientific changes."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from contextlib import ExitStack
from pathlib import Path

import h5py
import numpy as np
from netCDF4 import Dataset, num2date

FIELDS = ('RAINRATE', 'T2D', 'Q2D', 'PSFC', 'U2D', 'V2D', 'SWDOWN', 'LWDOWN')


def fingerprint(path):
    stat = Path(path).stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def verify_unchanged(original, repaired):
    """Compare compressed payloads without inflating unchanged meteorology."""
    count = 0
    with h5py.File(original) as a, h5py.File(repaired) as b:
        for name in FIELDS[1:]:
            x, y = a[name], b[name]
            if x.shape != y.shape or x.dtype != y.dtype or x.chunks != y.chunks:
                raise ValueError(f'Changed encoding: {name}')
            xc, yc = x.id.get_create_plist(), y.id.get_create_plist()
            if ([xc.get_filter(i) for i in range(xc.get_nfilters())] !=
                    [yc.get_filter(i) for i in range(yc.get_nfilters())]):
                raise ValueError(f'Changed filters: {name}')
            for key in ('_FillValue', 'scale_factor', 'add_offset', 'units'):
                if key in x.attrs or key in y.attrs:
                    np.testing.assert_array_equal(x.attrs.get(key), y.attrs.get(key))
            if x.chunks is None:
                # Rare contiguous fields: bounded-memory decoded comparison.
                for i in range(x.shape[0]):
                    np.testing.assert_array_equal(x[i], y[i])
                continue
            if x.id.get_num_chunks() != y.id.get_num_chunks():
                raise ValueError(f'Changed chunk allocation: {name}')
            for i in range(x.id.get_num_chunks()):
                offset = x.id.get_chunk_info(i).chunk_offset
                if x.id.read_direct_chunk(offset) != y.id.read_direct_chunk(offset):
                    raise ValueError(f'Changed compressed payload: {name} {offset}')
                count += 1
    return count


def write_day(original, windows, indices, destination, work, day, active, keep,
              metadata, *, reference=None):
    """Keep inputs immutable, stage on scratch, audit, then publish separately."""
    started = time.monotonic()
    destination, work = Path(destination), Path(work)
    if destination.exists() or destination.resolve() == Path(original).resolve():
        raise FileExistsError(destination)
    if len(windows) != 24 or len(indices) != 24:
        raise ValueError('Expected 24 records')
    paths = set(map(Path, windows)) | {Path(original)}
    if reference:
        paths.add(Path(reference))
    identities = {p: fingerprint(p) for p in paths}
    work.mkdir(parents=True, exist_ok=False)
    staged = work / destination.name
    timings = {}
    tick = time.monotonic()
    shutil.copy2(original, staged)
    timings['copy_original'] = time.monotonic() - tick
    tick = time.monotonic()
    with ExitStack() as stack:
        sources = {p: stack.enter_context(Dataset(p)) for p in set(map(Path, windows))}
        out = stack.enter_context(Dataset(staged, 'r+'))
        stamps = list(num2date(out['time'][:], out['time'].units))
        if [(s.year, s.month, s.day, s.hour) for s in stamps] != [
                (day.year, day.month, day.day, h) for h in range(24)]:
            raise ValueError('Original is not a complete calendar day')
        for h, (path, index) in enumerate(zip(windows, indices, strict=True)):
            src = sources[Path(path)]
            if str(getattr(src, 'prism_reconciliation_accepted', '')).lower() != 'true':
                raise ValueError('Unaccepted window')
            if num2date(src['time'][index], src['time'].units) != stamps[h]:
                raise ValueError('Window timestamp mismatch')
            values = np.ma.asarray(src['RAINRATE'][index], dtype='f8').filled(np.nan)
            if values.shape != active.shape or keep.shape != active.shape:
                raise ValueError('Mask shape mismatch')
            if (not np.all(np.isfinite(values[active])) or np.any(np.isfinite(values[~keep]))
                    or np.any(values[np.isfinite(values)] < 0)):
                raise ValueError('Invalid rainfall or coverage')
            out['RAINRATE'][h] = np.ma.masked_invalid(values)
        # Inherited precipitation diagnostics no longer describe this rain.
        for name in list(out.variables):
            if name.startswith('precip_') or name == 'forcing_qc_flags':
                out.renameVariable(name, 'historical_' + name)
        for name in list(out.ncattrs()):
            if name.startswith('prism_') or name in ('forcing_window', 'valid_time'):
                out.delncattr(name)
        for name in ('vmin', 'vmax'):
            if name in out['RAINRATE'].ncattrs():
                out['RAINRATE'].delncattr(name)
        out.setncatts(metadata)
        out.prism_reconciliation_accepted = 'false'
        out.rain_repair_writer = 'copy_replace_candidate_v1'
        timing = out.createVariable('prism_timing_source_id', 'u1', ('time', 'y', 'x'),
                                   zlib=True, complevel=2,
                                   chunksizes=(1, min(128, active.shape[0]), min(128, active.shape[1])))
        timing.flag_values = np.arange(5, dtype='u1')
        timing.flag_meanings = 'unconstrained baseline nldas uniform dry'
        for h, (path, index) in enumerate(zip(windows, indices, strict=True)):
            timing[h] = sources[Path(path)]['prism_timing_source_id'][index]
    timings['replace_rain'] = time.monotonic() - tick
    tick = time.monotonic()
    with ExitStack() as stack:
        sources = {p: stack.enter_context(Dataset(p)) for p in set(map(Path, windows))}
        out = stack.enter_context(Dataset(staged))
        ref = stack.enter_context(Dataset(reference)) if reference else None
        for h, (path, index) in enumerate(zip(windows, indices, strict=True)):
            for name in ('RAINRATE', 'prism_timing_source_id'):
                a, b = out[name][h], sources[Path(path)][name][index]
                np.testing.assert_array_equal(np.ma.getmaskarray(a), np.ma.getmaskarray(b))
                np.testing.assert_array_equal(a.compressed(), b.compressed())
            if ref:
                for name in FIELDS:
                    a, b = out[name][h], ref[name][h]
                    np.testing.assert_array_equal(np.ma.getmaskarray(a), np.ma.getmaskarray(b))
                    np.testing.assert_array_equal(a.compressed(), b.compressed())
    timings['rain_readback_and_reference'] = time.monotonic() - tick
    tick = time.monotonic()
    chunks = verify_unchanged(original, staged)
    timings['unchanged_compressed_audit'] = time.monotonic() - tick
    with Dataset(staged, 'r+') as out:
        out.prism_reconciliation_accepted = 'true'
    if any(fingerprint(p) != identity for p, identity in identities.items()):
        raise ValueError('Input changed during repair')
    tick = time.monotonic()
    checksum = digest(staged)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    with staged.open('rb') as source, partial.open('xb') as target:
        shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
    if digest(partial) != checksum:
        raise ValueError('Publication checksum mismatch')
    # Hard link is atomic and refuses to overwrite an existing candidate.
    os.link(partial, destination)
    partial.unlink()
    staged.unlink()
    timings['publication'] = time.monotonic() - tick
    return {'path': str(destination), 'sha256': checksum, 'phase_seconds': timings,
            'unchanged_chunks_verified': chunks, 'reference_equal': bool(reference),
            'elapsed_seconds': time.monotonic() - started}
