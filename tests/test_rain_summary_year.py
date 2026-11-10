import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1]/'bin'
sys.path.insert(0, str(BIN))
spec = importlib.util.spec_from_file_location('rain_summary_year', BIN/'run_rain_summary_year.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_preceding_deferred_month():
    assert module.month_tasks(1990, date(1990, 12, 1), date(2000, 12, 30)) == [(date(1990, 12, 1), None)]


def test_complete_interior_year():
    tasks = module.month_tasks(1996, date(1990, 12, 1), date(2000, 12, 30))
    assert len(tasks) == 12
    assert tasks[-1] == (date(1996, 12, 1), None)


def test_last_month_partial():
    tasks = module.month_tasks(2000, date(1990, 12, 1), date(2000, 12, 30))
    assert len(tasks) == 12
    assert tasks[-1] == (date(2000, 12, 1), date(2000, 12, 30))


def test_outside_range_rejected():
    with pytest.raises(ValueError):
        module.month_tasks(2001, date(1990, 12, 1), date(2000, 12, 30))
