import numpy as np
import pytest

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_safeguards import (
    allocate_daily_depth,
    audit_direct_depth,
    direct_conservative_depth,
)


def operator():
    # Unequal covered areas: normalized transpose alone would be wrong.
    return ConservativeOperator(3, 2, np.array([0, 1, 1, 2]),
                                np.array([0, 0, 1, 1]), np.array([.5, .5, .25, .75]))


def test_overlap_conservation_and_stored_allocation():
    op = operator()
    target, area = np.array([0., 8.]), np.array([2., 4.])
    depth, source_area = direct_conservative_depth(target, op, area)
    np.testing.assert_allclose(depth, [0, 4, 8])
    assert np.sum(depth * source_area) == np.sum(target * area)
    hourly, _ = allocate_daily_depth(depth, np.zeros((24, 3)), np.zeros((24, 3)))
    stored = (hourly / 3600).astype('f4').astype('f8').sum(axis=0) * 3600
    assert audit_direct_depth(stored, target, op, area)['accepted']
    # Cell-level inverse fit is intentionally not exact.
    assert not np.allclose(op.apply(depth), target)


def test_masks_and_invalid_inputs():
    op = operator()
    depth, _ = direct_conservative_depth([np.nan, 8], op, [2, 4])
    assert np.isnan(depth[0])
    np.testing.assert_allclose(depth[1:], 8)
    for target, area in [([-1, 8], [2, 4]), ([0, 8], [2, np.nan]),
                         ([np.nan, np.nan], [2, 4])]:
        with pytest.raises(ValueError):
            direct_conservative_depth(target, op, area)


@pytest.mark.parametrize('actual', [[0, np.nan, 8], [0, -1, 8], [0, 5, 8]])
def test_corrupt_storage_fails(actual):
    assert not audit_direct_depth(actual, [0, 8], operator(), [2, 4])['accepted']


def test_dry_and_unsupported_target():
    op = operator()
    assert audit_direct_depth([0, 0, 0], [0, 0], op, [2, 4])['accepted']
    broken = ConservativeOperator(1, 2, np.array([0]), np.array([0]), np.ones(1))
    with pytest.raises(ValueError, match='Missing overlap'):
        direct_conservative_depth([0, 8], broken, [2, 4])
