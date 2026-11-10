import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
spec = importlib.util.spec_from_file_location('repaired_summaries', BIN/'subset_repaired_summaries.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_complete_and_leap_years(tmp_path):
    assert len(module.selected_paths(tmp_path, 1986)) == 365 + 12
    assert len(module.selected_paths(tmp_path, 1988)) == 366 + 12


def test_unrepaired_boundary_excludes_final_day_and_month(tmp_path):
    paths = module.selected_paths(tmp_path, 1990, date(1990, 12, 30))
    assert len(paths) == 364 + 11
    assert not any(p.name.startswith('19901231') for p in paths)
    assert not any(p.name.startswith('199012') and p.name.endswith('.monthly') for p in paths)
    assert any(p.name.startswith('19901230') for p in paths)


@pytest.mark.parametrize('year,end', [(1980, None), (1990, date(1991, 1, 1)),
                                     (1990, date(1989, 12, 31))])
def test_reject_invalid_ranges(tmp_path, year, end):
    with pytest.raises(ValueError):
        module.selected_paths(tmp_path, year, end)
