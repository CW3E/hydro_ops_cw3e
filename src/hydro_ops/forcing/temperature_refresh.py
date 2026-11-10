"""Coupled temperature replacement with exact preservation of other payloads."""
import h5py
import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.physics import (
    cosgrove_atmospheric_emissivity,
    relative_humidity_from_specific_humidity,
    specific_humidity_from_relative_humidity,
)

CHANGED = ('T2D', 'Q2D', 'LWDOWN')


def coupled(temperature, humidity, pressure, longwave, new_temperature):
    """Preserve relative humidity and the existing longwave emission multiplier."""
    rh = relative_humidity_from_specific_humidity(humidity, temperature, pressure, phase='water')
    q = specific_humidity_from_relative_humidity(rh, new_temperature, pressure, phase='water')
    old = cosgrove_atmospheric_emissivity(temperature, humidity, pressure)*temperature**4
    new = cosgrove_atmospheric_emissivity(new_temperature, q, pressure)*new_temperature**4
    factor = np.divide(longwave, old, out=np.full(old.shape, np.nan), where=np.isfinite(old) & (old > 0))
    return {'T2D': new_temperature, 'Q2D': q, 'LWDOWN': factor*new}


def unchanged_payloads(original, candidate):
    """Check every non-replaced HDF5 dataset, including rainfall and QC."""
    chunks = 0
    with h5py.File(original) as a, h5py.File(candidate) as b:
        if set(a) != set(b):
            raise ValueError('Dataset inventory changed')
        for name in a:
            if name in CHANGED:
                continue
            x, y = a[name], b[name]
            if x.shape != y.shape or x.dtype != y.dtype or x.chunks != y.chunks:
                raise ValueError(f'Encoding changed: {name}')
            if x.chunks is None:
                np.testing.assert_array_equal(x[()], y[()])
            else:
                if x.id.get_num_chunks() != y.id.get_num_chunks():
                    raise ValueError(f'Chunk allocation changed: {name}')
                for i in range(x.id.get_num_chunks()):
                    offset = x.id.get_chunk_info(i).chunk_offset
                    if x.id.read_direct_chunk(offset) != y.id.read_direct_chunk(offset):
                        raise ValueError(f'Payload changed: {name}/{offset}')
                    chunks += 1
    return chunks


def replace_hours(original, staged, mapping, active, keep, provenance):
    """Replace disjoint selected hours once; verify all other hours and payloads."""
    import json
    import shutil
    from contextlib import ExitStack

    def field(ds, name, h):
        return np.ma.asarray(ds[name][h], dtype='f8').filled(np.nan)

    if not mapping or not set(mapping) <= set(range(24)):
        raise ValueError('Invalid selected hours')
    shutil.copy2(original, staged)
    with ExitStack() as stack:
        old = stack.enter_context(Dataset(original))
        sources = {p:stack.enter_context(Dataset(p)) for p in {p for p,_ in mapping.values()}}
        times = num2date(old['time'][:],old['time'].units)
        if len(times) != 24 or [t.hour for t in times] != list(range(24)) or len({(t.year,t.month,t.day) for t in times}) != 1:
            raise ValueError('Expected a complete calendar day')
        def expected(h):
            if h not in mapping:
                return {n:field(old,n,h) for n in CHANGED}
            p,i = mapping[h]
            source = sources[p]
            if num2date(source['time'][i],source['time'].units) != times[h]:
                raise ValueError('Temperature-window time mismatch')
            t = field(old,'T2D',h)
            target = np.where(keep & np.isfinite(t),field(source,'T2D',i),np.nan)
            return coupled(t,field(old,'Q2D',h),field(old,'PSFC',h),field(old,'LWDOWN',h),target)
        with Dataset(staged,'r+') as out:
            for h in sorted(mapping):
                for name,value in expected(h).items():
                    if not np.all(np.isfinite(value[active])) or np.any(np.isfinite(value[~keep])):
                        raise ValueError(f'Coverage failed: {h}/{name}')
                    if name != 'T2D' and np.any(value[np.isfinite(value)] < 0):
                        raise ValueError(f'Negative field: {name}')
                    out[name][h] = np.ma.masked_invalid(value)
            out.temperature_refresh_provenance = json.dumps(provenance,sort_keys=True)
        with Dataset(staged) as out:
            for h in range(24):
                for name,value in expected(h).items():
                    np.testing.assert_array_equal(field(out,name,h).astype('f4'),value.astype('f4'))
    return unchanged_payloads(original,staged)
