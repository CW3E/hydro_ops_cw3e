"""Calendar-centered soil-water/SWE climatology and approximate percentile ranks."""
from datetime import date, timedelta

import numpy as np

REFERENCE_START = date(1979, 10, 1)
REFERENCE_END = date(2025, 9, 30)
PERCENTILES = np.arange(0.5, 100, 1, dtype=np.float64)
THICKNESSES = np.array([0.1, 0.3, 0.6, 1.0], dtype=np.float64)
METHOD = 'median_unbiased'  # Hyndman-Fan type 8; CDO pm=r8.


def dates(first, last):
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def calendar_windows(input_dates, start=REFERENCE_START, end=REFERENCE_END, half_window=15):
    """Return 366 arrays of daily indices; February 29 has no direct centers.

    Adjacent windows retain leap-day observations and use actual calendar days.
    Padding supplies observations only; all centers are within [start, end].
    """
    if not input_dates or start > end or half_window < 0:
        raise ValueError('Invalid reference interval/window')
    if input_dates != dates(input_dates[0], input_dates[-1]):
        raise ValueError('Input dates must be unique, sorted and continuous')
    if input_dates[0] > start - timedelta(days=half_window) or input_dates[-1] < end + timedelta(days=half_window):
        raise ValueError('Missing 15-day boundary padding')
    calendar = dates(date(2000, 1, 1), date(2000, 12, 31))
    keys = {(d.month, d.day): i for i, d in enumerate(calendar)}
    indices = [[] for _ in calendar]
    for center in dates(start, end):
        if (center.month, center.day) == (2, 29):
            continue
        offset = (center - input_dates[0]).days
        indices[keys[center.month, center.day]].extend(range(offset-half_window, offset+half_window+1))
    return [np.asarray(row, dtype=np.int64) for row in indices]


def column_soil_water(values, thicknesses=THICKNESSES, layer_axis=-2):
    """Depth-weighted water storage in mm; one missing layer invalidates the column."""
    values = np.ma.asarray(values, dtype=np.float64).filled(np.nan)
    weights = np.asarray(thicknesses, dtype=np.float64)
    if values.shape[layer_axis] != len(weights) or np.any(weights <= 0):
        raise ValueError('Soil layer/thickness mismatch')
    shape = [1] * values.ndim
    shape[layer_axis] = len(weights)
    invalid = ~np.isfinite(values) | (values < 0) | (values > 1)
    result = np.sum(values * weights.reshape(shape), axis=layer_axis) * 1000
    return np.where(invalid.any(axis=layer_axis), np.nan, result)


def statistics(values, windows, percentiles=PERCENTILES, min_samples=1):
    """Compute all quantiles in one call per calendar date for one spatial tile."""
    values = np.ma.asarray(values, dtype=np.float64).filled(np.nan)
    levels = np.asarray(percentiles, dtype=np.float64)
    if values.ndim != 3 or len(windows) != 366 or min_samples < 1:
        raise ValueError('Expected time/y/x data and a 366-day calendar')
    if levels.ndim != 1 or np.any(np.diff(levels) <= 0) or np.any((levels < 0) | (levels > 100)):
        raise ValueError('Invalid percentile levels')
    ny, nx = values.shape[1:]
    q = np.full((366, len(levels), ny, nx), np.nan, dtype=np.float32)
    count = np.zeros((366, ny, nx), dtype=np.int32)
    for day, indices in enumerate(windows):
        if day == 59 or not len(indices):
            continue
        sample = values[indices].reshape(len(indices), -1)
        sample[~np.isfinite(sample)] = np.nan
        n = np.isfinite(sample).sum(axis=0)
        count[day] = n.reshape(ny, nx)
        valid = n >= min_samples
        if valid.any():
            result = np.full((len(levels), ny*nx), np.nan)
            result[:, valid] = np.nanpercentile(sample[:, valid], levels, axis=0, method=METHOD)
            q[day] = result.reshape(len(levels), ny, nx)
    # Interpolate thresholds, never pooled samples or reported ranks.
    q[59] = (q[58].astype('f8') + q[60].astype('f8')) / 2
    count[59] = -1  # No empirical Feb-29 distribution; neighbors retain their counts.
    return q, count


def percentile_rank(values, quantiles, percentiles=PERCENTILES, *, swe_minimum=None):
    """Approximate rank, clipped to table endpoints; duplicate thresholds use midranks.

    SWE observations <= the configured threshold are missing in rank maps only.
    Zeros remain in the reference distribution. This is quantile-table inversion,
    not an exact empirical CDF; extreme tails are censored at 0.5/99.5 by default.
    """
    values = np.ma.asarray(values, dtype=float).filled(np.nan)
    q = np.ma.asarray(quantiles, dtype=float).filled(np.nan)
    levels = np.asarray(percentiles, dtype=float)
    if q.shape != (len(levels), *values.shape):
        raise ValueError('Quantile/value dimensions disagree')
    if swe_minimum is not None and (not np.isfinite(swe_minimum) or swe_minimum < 0):
        raise ValueError('Invalid SWE threshold')
    ranks = np.full(values.shape, np.nan, dtype='f4')
    level_sums = np.concatenate(([0.], np.cumsum(levels)))
    for i, value in enumerate(values.flat):
        if not np.isfinite(value) or (swe_minimum is not None and value <= swe_minimum):
            continue
        thresholds = q.reshape(len(levels), -1)[:, i]
        if not np.isfinite(thresholds).all():
            continue
        if np.any(np.diff(thresholds) < 0):
            raise ValueError('Nonmonotonic quantile table')
        unique, first, counts = np.unique(thresholds, return_index=True, return_counts=True)
        mid = (level_sums[first+counts] - level_sums[first]) / counts
        ranks.flat[i] = np.interp(value, unique, mid, left=levels[0], right=levels[-1])
    return ranks


def tile_windows(shape, tile_shape):
    """Nonoverlapping half-open index windows, including short boundary tiles."""
    if min(*shape, *tile_shape) < 1:
        raise ValueError('Positive grid and tile shapes required')
    return [(y, min(y+tile_shape[0], shape[0]), x, min(x+tile_shape[1], shape[1]))
            for y in range(0, shape[0], tile_shape[0])
            for x in range(0, shape[1], tile_shape[1])]


def assemble_tiles(tiles, shape):
    """Small-array validation oracle; production can use the same indexed writes."""
    first = tiles[0][1]
    result = np.empty((*first.shape[:-2], *shape), dtype=first.dtype)
    covered = np.zeros(shape, dtype=bool)
    for (y0, y1, x0, x1), values in tiles:
        if not (0 <= y0 < y1 <= shape[0] and 0 <= x0 < x1 <= shape[1]):
            raise ValueError('Tile outside grid')
        if covered[y0:y1, x0:x1].any() or values.shape != (*first.shape[:-2], y1-y0, x1-x0):
            raise ValueError('Overlapping/malformed tile')
        result[..., y0:y1, x0:x1] = values
        covered[y0:y1, x0:x1] = True
    if not covered.all():
        raise ValueError('Incomplete tile coverage')
    return result
