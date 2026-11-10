from pathlib import Path

import numpy as np
import pytest
from test_nwm_forcing_coordinator import load_script


@pytest.fixture(autouse=True)
def script_imports(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))


def test_shift_does_not_wrap():
    m = load_script('diagnose_temperature_target_alignment.py')
    a = np.arange(20., dtype=float).reshape(4, 5)
    b = m.shifted(a, 1, -1)
    assert np.isnan(b[0]).all()
    assert np.isnan(b[:, -1]).all()
    np.testing.assert_array_equal(b[1:, :-1], a[:-1, 1:])
    np.testing.assert_array_equal(a, np.arange(20.).reshape(4, 5))


def test_comparison_uses_same_valid_cells():
    m = load_script('diagnose_temperature_target_alignment.py')
    r = m.comparison(np.array([10., 100.]), np.array([9., np.nan]), np.array([8., 0.]))
    assert r['cells'] == 1
    assert r['alternative_error_k']['rms'] == 1
    assert r['same_cell_current_error_k']['rms'] == 2
    with pytest.raises(ValueError, match='finite nonempty'):
        m.comparison(np.array([np.nan]), np.array([1.]), np.array([1.]))
