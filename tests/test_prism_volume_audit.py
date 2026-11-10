import numpy as np
import pytest

from hydro_ops.forcing.prism_volume_audit import region_totals


def test_area_weighted_and_missing_excluded():
    result = region_totals([2, 4, np.nan], [3, 3, np.nan], [1, 3, 0])
    assert result['prism_mean_mm'] == 3.5
    assert result['forcing_mean_mm'] == 3
    assert result['bias_mm'] == -.5
    assert result['bias_percent'] == pytest.approx(-100 / 7)


def test_dry_reference_no_percentage():
    result = region_totals([0, 0], [0, .01], [1, 1])
    assert result['bias_percent'] is None
    assert result['bias_mm'] == .005


def test_invalid_support_rejected():
    for target, estimate, weights in [([1], [np.nan], [1]), ([1], [1], [0]),
                                      ([1], [1], [-1]), ([1], [-1], [1])]:
        with pytest.raises(ValueError):
            region_totals(target, estimate, weights)
