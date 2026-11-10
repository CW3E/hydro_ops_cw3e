import importlib
from datetime import date
from pathlib import Path

import pytest


def test_partial_plan(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    tool = importlib.import_module('prism_repair_wave')
    tasks, boundary = tool.month_tasks(date(2021, 1, 1), date(2026, 4, 1), date(2026, 4, 17))
    assert len(tasks) == 64
    assert tasks[-1] == {'start': '2026-04-01', 'days': 17}
    assert boundary == date(2026, 4, 18)
    publisher = importlib.import_module('publish_prism_repair_wave')
    assert publisher.month_range(date(2021, 1, 1), 63, date(2026, 4, 17)) == (
        date(2026, 4, 1), date(2026, 4, 17))
    assert sum(t['days'] for t in tasks) == (date(2026, 4, 18)-date(2021, 1, 1)).days
    tasks, boundary = tool.month_tasks(date(2024, 2, 1), date(2024, 2, 1))
    assert tasks == [{'start': '2024-02-01', 'days': 29}]
    assert boundary == date(2024, 3, 1)
    with pytest.raises(ValueError):
        tool.month_tasks(date(2026, 4, 1), date(2026, 4, 1), date(2026, 5, 1))
