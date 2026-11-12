from datetime import date
from pathlib import Path

from test_nwm_forcing_coordinator import load_script


def test_timeline_keeps_overlap_and_prefers_retro(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('build_web_raster_collection.py')
    common = {'product': 'nwm', 'variable': 'swe_percentile', 'resolution': 'daily', 'date': '2026-04-01'}
    rows = [{**common, 'id': 'nrt', 'stream': 'nrt'}, {**common, 'id': 'retro', 'stream': 'retro'},
            {**common, 'date': '2026-04-19', 'id': 'new', 'stream': 'nrt'}]
    result = module.choose_timeline(rows)
    assert result[0]['preferredAsset'] == 'retro'
    assert set(result[0]['availableAssets']) == {'retro', 'nrt'}
    assert result[1]['preferredAsset'] == 'new'
    assert module.next_month(date(2024, 2, 1)) == date(2024, 3, 1)
