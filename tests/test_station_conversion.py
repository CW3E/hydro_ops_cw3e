import json

import pytest

from hydro_ops.observations import station_conversion as conversion
from hydro_ops.observations.station_migration import inventory
from hydro_ops.observations.station_store import StationTable


def fixture(root, monkeypatch):
    source = root/'observations/processed/cdec/fnf/daily/AAA'
    source.mkdir(parents=True)
    (source/'2020.csv').write_text('original source')
    (source/'manifest.json').write_text(json.dumps({'status': 'passed', 'station_id': 'AAA', 'resolution': 'daily'}))
    meta = root/'metadata.json'
    meta.write_text('{}')
    plan = root/'inventory.json'
    plan.write_text(json.dumps(inventory(root)))
    campaign = root/'observations/work/station-migration/test'
    manifest = conversion.prepare(root, campaign, plan, meta, meta)
    entry = manifest['entries'][0]
    table = StationTable({'provider': 'cdec', 'station_id': 'AAA', 'product': 'full_natural_flow',
        'sensor': '8', 'resolution': 'daily', 'units_si': 'm3 s-1', 'timezone': 'PST',
        'interval_semantics': 'provider labels',
        'mapping': {'series': 'series', 'date': 'date', 'value': 'value', 'status': 'status'},
        'provenance': [{'path': str((source/'2020.csv').relative_to(root)),
                        'sha256': conversion.digest(source/'2020.csv')}]},
        ['series', 'date', 'value', 'status'],
        [{'series': '8', 'date': '2020-01-01', 'value': '0', 'status': 'numeric'}])
    monkeypatch.setattr(conversion, 'archive_table', lambda *a, **kw: table)
    args = (str(root), str(campaign), entry, manifest['metadata'], conversion.digest(campaign/'campaign.json'))
    return args, source, campaign


def test_accept_resume_and_orphan_recovery(tmp_path, monkeypatch):
    args, source, campaign = fixture(tmp_path, monkeypatch)
    assert conversion.convert(args)['status'] == 'accepted'
    target = next((campaign/'stations').glob('*.nc'))
    before = target.stat().st_mtime_ns
    assert conversion.convert(args)['status'] == 'skipped_verified'
    next((campaign/'receipts').glob('*.json')).unlink()
    assert conversion.convert(args)['status'] == 'accepted'
    assert target.stat().st_mtime_ns == before
    assert (source/'2020.csv').read_text() == 'original source'


@pytest.mark.parametrize('change', ['source', 'output', 'metadata', 'new_year'])
def test_changed_inputs_or_outputs_fail_closed(tmp_path, monkeypatch, change):
    args, source, campaign = fixture(tmp_path, monkeypatch)
    assert conversion.convert(args)['status'] == 'accepted'
    if change == 'source':
        (source/'2020.csv').write_text('revised')
    elif change == 'new_year':
        (source/'2021.csv').write_text('new')
    elif change == 'metadata':
        (tmp_path/'metadata.json').write_text('{"changed":true}')
    else:
        next((campaign/'stations').glob('*.nc')).write_bytes(b'corrupt')
    assert conversion.convert(args)['status'] == 'failed'


def test_conflicting_campaign_and_escape_rejected(tmp_path, monkeypatch):
    _, _, campaign = fixture(tmp_path, monkeypatch)
    (tmp_path/'metadata.json').write_text('{"changed":true}')
    with pytest.raises(ValueError, match='Campaign inputs differ'):
        conversion.prepare(tmp_path, campaign, tmp_path/'inventory.json',
                           tmp_path/'metadata.json', tmp_path/'metadata.json')
    with pytest.raises(ValueError, match='staged'):
        conversion.prepare(tmp_path, tmp_path/'production', tmp_path/'inventory.json',
                           tmp_path/'metadata.json', tmp_path/'metadata.json')


def test_balanced_batch_excludes_completed_and_blocked():
    entries = []
    for provider in ('cdec', 'usgs'):
        for i in range(100):
            entries.append({'provider': provider, 'product': 'flow', 'sensor': '1', 'resolution': 'daily',
                            'task_id': f'{provider}-{i}', 'status': 'candidate' if i else 'blocked'})
    report = {'entries': entries}
    tasks = conversion.select_batch(report, size=128, excluded={'cdec-1', 'usgs-1'})
    assert len(tasks) == len(set(tasks)) == 128
    assert all(t not in tasks for t in ('cdec-0', 'usgs-0', 'cdec-1', 'usgs-1'))
    assert sum(t.startswith('cdec') for t in tasks) == 64
    assert tasks == conversion.select_batch(report, size=128, excluded={'cdec-1', 'usgs-1'})
    with pytest.raises(ValueError):
        conversion.select_batch(report, size=129)


def test_exclusion_requires_complete_matching_acceptance(tmp_path, monkeypatch):
    args, _, campaign = fixture(tmp_path, monkeypatch)
    task = args[2]['task_id']
    receipt = campaign/'acceptance.json'
    receipt.write_text(json.dumps({'status': 'passed', 'passes': [[{'task_id': task, 'status': 'accepted'}]]}))
    assert conversion.completed_tasks(tmp_path, campaign, tmp_path/'inventory.json') == {task}
    receipt.write_text(json.dumps({'status': 'passed', 'passes': [[]]}))
    with pytest.raises(ValueError, match='incomplete'):
        conversion.completed_tasks(tmp_path, campaign, tmp_path/'inventory.json')
