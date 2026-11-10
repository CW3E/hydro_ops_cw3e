import importlib
from itertools import pairwise
from pathlib import Path

import pytest


def test_partial_year_bridge(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = importlib.import_module('continue_yampa_retro')
    assert module.previous_marker(2020) == '20200101-20201231-passed.json'
    assert module.previous_marker(2021) == '20201231-20210101-passed.json'
    assert module.previous_marker(2022) == '2021-passed.json'
    intervals = [module.interval(y) for y in range(2020, 2027)]
    assert str(intervals[0][0]) == '2020-12-31 00:00:00'
    assert str(intervals[-1][1]) == '2026-04-17 00:00:00'
    assert (intervals[0][1]-intervals[0][0]).days == 1
    assert all(a[1] == b[0] for a, b in pairwise(intervals))
    with pytest.raises(ValueError):
        module.interval(2027)


def test_domain_continuation_scopes(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = importlib.import_module('continue_yampa_retro')
    assert module.continuation('b120') == (2020, 'b120-extension-20201231-20260417')
    assert module.continuation('ucrb') == (2026, 'ucrb-extension-20260101-20260417')
    assert module.previous_marker(2026) == '2025-passed.json'
    first, end = module.interval(2026)
    assert str(first) == '2026-01-01 00:00:00'
    assert str(end) == '2026-04-17 00:00:00'
