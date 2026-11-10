import importlib.util
import sys
from pathlib import Path

import numpy as np


def test_failure_categories(monkeypatch):
    folder = Path(__file__).resolve().parents[1]/'bin'
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('coverage_diagnostic', folder/'diagnose_rain_coverage.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    values = np.array([[np.nan, 0., -1., np.nan, 2.]])
    active = np.array([[True, False, True, False, True]])
    keep = np.array([[True, False, True, False, True]])
    bad = module.failures(values, active, keep)
    assert {k: int(v.sum()) for k, v in bad.items()} == {
        'missing_active': 1, 'outside_envelope': 1, 'negative': 1}
