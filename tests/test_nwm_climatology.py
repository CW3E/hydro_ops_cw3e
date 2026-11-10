"""Scientific invariants for calendar-centered land-state climatologies."""
from datetime import date, timedelta

import numpy as np
import pytest

from hydro_ops.wrf_hydro.climatology import (
    REFERENCE_END,
    REFERENCE_START,
    assemble_tiles,
    calendar_windows,
    column_soil_water,
    dates,
    percentile_rank,
    statistics,
    tile_windows,
)


def test_reference_padding_leap_alignment_and_center_year_counts():
    days = dates(REFERENCE_START-timedelta(days=15), REFERENCE_END+timedelta(days=15))
    windows = calendar_windows(days)
    assert days[0] == date(1979, 9, 16) and days[-1] == date(2025, 10, 15)
    assert not len(windows[59])
    assert all(len(w) == 1426 for i, w in enumerate(windows) if i != 59)
    assert date(1980, 2, 29) in [days[i] for i in windows[58]]
    assert days[windows[0][0]] == date(1979, 12, 17)
    assert days[windows[60][0]] == date(1980, 2, 15)
    # Later March centers remain March 1 rather than shifting after leap years.
    assert days[windows[60][31+15]] == date(1981, 3, 1)
    with pytest.raises(ValueError, match='padding'):
        calendar_windows(days[1:])
    with pytest.raises(ValueError, match='continuous'):
        calendar_windows(days[:10]+days[11:])


def test_weighted_storage_and_missing_layers():
    soil = np.array([[[.1], [.2], [.3], [.4]]])
    assert column_soil_water(soil).item() == pytest.approx(650.)
    assert column_soil_water(np.full((2, 4, 3), .25)).tolist() == [[500.]*3]*2
    masked = np.ma.array(soil, mask=False); masked.mask[0, 2, 0] = True
    assert np.isnan(column_soil_water(masked)).all()
    soil[0, 2, 0] = 1.5
    assert np.isnan(column_soil_water(soil)).all()


def test_type8_missing_values_sample_counts_and_feb29_interpolation():
    windows = [np.arange(5) for _ in range(366)]; windows[59] = np.array([], dtype=int)
    values = np.array([0., 1., 2., 3., np.nan]).reshape(5, 1, 1)
    q, n = statistics(values, windows, [25, 50, 75])
    # Independent type-8 positions: h=(n+1/3)p-2/3, in zero-based samples.
    np.testing.assert_allclose(q[0, :, 0, 0], [5/12, 1.5, 31/12])
    assert n[0, 0, 0] == 4 and n[59, 0, 0] == -1
    windows[58] = np.array([0, 1]); windows[60] = np.array([2, 3])
    q, _ = statistics(values, windows, [50])
    assert q[59, 0, 0, 0] == (q[58, 0, 0, 0]+q[60, 0, 0, 0])/2
    q, n = statistics(np.full((5, 1, 1), np.nan), windows, [50])
    assert np.isnan(q).all() and n[0, 0, 0] == 0


def test_swe_threshold_is_rank_only_and_tails_are_censored():
    values = np.array([[0., 1., 2., 100.]])
    levels = np.array([.5, 50., 99.5])
    q = np.broadcast_to(np.array([0., 0., 10.])[:, None, None], (3, 1, 4))
    ranks = percentile_rank(values, q, levels, swe_minimum=1.)
    assert np.isnan(ranks[0, :2]).all()
    assert ranks[0, 2] == pytest.approx(40.1)
    assert ranks[0, 3] == 99.5
    assert percentile_rank(values, q, levels)[0, 0] == 25.25


def test_tiles_reassemble_all_axes_and_reject_gaps_or_overlaps():
    a = np.arange(2*3*9*11).reshape(2, 3, 9, 11)
    pieces = [(w, a[..., w[0]:w[1], w[2]:w[3]]) for w in tile_windows((9, 11), (4, 5))]
    np.testing.assert_array_equal(assemble_tiles(pieces, (9, 11)), a)
    with pytest.raises(ValueError, match='Incomplete'):
        assemble_tiles(pieces[:-1], (9, 11))
    with pytest.raises(ValueError, match='Overlapping'):
        assemble_tiles(pieces+[pieces[0]], (9, 11))
