from pathlib import Path

from test_rain_repair_endpoints import module


def test_window_maps_to_both_calendar_days(monkeypatch):
    mod = module(monkeypatch, 'review_precipitation_pipeline')
    assert mod.replacement_days(Path('windows/2026/01/20260101.LDASIN_DOMAIN1')) == ['2025-12-31', '2026-01-01']
    assert mod.replacement_days(Path('hourly/2026/01/20260101.LDASIN_DOMAIN1')) == ['2026-01-01']
    assert mod.replacement_days(Path('weights.nc')) == []
