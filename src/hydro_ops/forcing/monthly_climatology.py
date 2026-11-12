"""Exact calendar-month forcing quantiles with bounded temporary memory."""
import numpy as np

LEVELS = np.arange(0.5, 100, dtype='f8')
METHOD = 'median_unbiased'


def quantiles(values, min_samples=30, block_cells=4096):
    """Return type-8 thresholds and finite sample counts; zeros remain samples."""
    a = np.ma.asarray(values, dtype='f4').filled(np.nan)
    if a.ndim != 3 or min_samples < 1 or block_cells < 1:
        raise ValueError('Expected sample/y/x data and positive limits')
    shape = a.shape[1:]
    a = a.reshape(a.shape[0], -1)
    counts = np.isfinite(a).sum(axis=0).astype('i2')
    out = np.full((len(LEVELS), a.shape[1]), np.nan, dtype='f4')
    for start in range(0, a.shape[1], block_cells):
        stop = min(start + block_cells, a.shape[1])
        sample = a[:, start:stop].astype('f8')
        sample[~np.isfinite(sample)] = np.nan
        n = counts[start:stop]
        complete = n == a.shape[0]
        partial = (n >= min_samples) & ~complete
        complete &= n >= min_samples
        if complete.any():
            out[:, start:stop][:, complete] = np.percentile(
                sample[:, complete], LEVELS, axis=0, method=METHOD)
        if partial.any():
            out[:, start:stop][:, partial] = np.nanpercentile(
                sample[:, partial], LEVELS, axis=0, method=METHOD)
    return out.reshape(len(LEVELS), *shape), counts.reshape(shape)
