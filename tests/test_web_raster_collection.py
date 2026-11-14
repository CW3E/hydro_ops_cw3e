import json
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


def test_v4_isolated_validation_fixtures(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('build_web_raster_collection.py')
    module.validation_fixtures(tmp_path)
    report = json.loads((tmp_path/'validation-fixtures/report.json').read_text())
    assert report['synthetic'] and report['status'] == 'passed'
    assert report['february29']['threshold'] == 1.5
    assert report['quality']['qualityCounts']['sourceCells']['statisticUnavailable'] == 1


def test_v4_structured_scale():
    from hydro_ops.forcing.web_contract import variables
    spec = variables()
    assert spec['unitVocabulary']['temperature'] == 'Cel'
    for name, item in spec['variables'].items():
        if name.endswith('_percentile'):
            assert item['numericScale']['legendThresholdScale'] == [0, 100]
            assert item['numericScale']['multiplyBy100ForDisplay'] is False
    assert spec['aggregateCompleteness']['daily']['requiredSamples'] == 24


def test_exclusive_stream_selection(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('build_web_raster_collection.py')
    base = {'product': 'forcing', 'resolution': 'daily', 'date': '2026-04-01'}
    rows = [{**base, 'stream': 'nrt'}, {**base, 'stream': 'retro'},
            {**base, 'date': '2026-04-02', 'stream': 'nrt'},
            {**base, 'product': 'nwm', 'stream': 'nrt'}]
    for inputs in (rows, list(reversed(rows))):
        selected = module.select_exclusive_streams(inputs)
        assert len(selected) == 3
        assert [a['stream'] for a in selected if a['product'] == 'forcing' and a['date'] == '2026-04-01'] == ['retro']
