from pathlib import Path

import numpy as np
import pytest
from test_nwm_forcing_coordinator import load_script


def test_midpoint_and_coverage(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    m = load_script('audit_independent_temperature_target.py')
    lo = np.array([270., 270.])
    hi = np.array([280., 280.])
    active = np.array([True, True])
    assert m.evaluate(lo, hi, lo-1, hi+1, active)['result'] == 'midpoint_match'
    assert m.evaluate(lo+1, hi, lo, hi, active)['result'] == 'mismatch_or_missing'
    lo[1] = np.nan
    r = m.evaluate(lo, hi, lo, hi, active)
    assert r['incomplete_active_cells'] == 1
    assert r['result'] == 'mismatch_or_missing'
    with pytest.raises(ValueError, match='Empty'):
        m.evaluate(lo, hi, lo, hi, np.zeros(2, bool))
