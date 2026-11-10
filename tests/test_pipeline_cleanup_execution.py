import os

import pytest
from test_rain_repair_endpoints import module


def test_exact_array_paths_only(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'execute_pipeline_cleanup')
    path = tmp_path/'forcing/work/prism-rain-pipeline/123_4/windows/2026/04/20260412.LDASIN_DOMAIN1'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'abc')
    row = {'path': str(path), 'file': mod.file_record(path)}
    assert mod.validate_path(row, tmp_path) == path
    os.link(path, path.with_name('alias'))
    with pytest.raises(ValueError, match='linked'):
        mod.validate_path(row, tmp_path)
    report = path.with_name('20260412.json')
    report.write_text('{}')
    with pytest.raises(ValueError, match='Unexpected'):
        mod.validate_path({'path': str(report), 'file': mod.file_record(report)}, tmp_path)


def test_retired_candidate_publication_refused(tmp_path):
    from test_repair_transaction import fixture_plan

    from hydro_ops.forcing.repair_transaction import atomic_json, publish
    path, plan = fixture_plan(tmp_path)
    campaign = tmp_path/'forcing/work/prism-rain-pipeline/123_4'
    campaign.mkdir(parents=True)
    atomic_json(campaign/'RETIRED.json', {'status': 'retiring'})
    plan['candidate'] = str(campaign/'hourly/missing.LDASIN_DOMAIN1')
    atomic_json(path, plan)
    with pytest.raises(ValueError, match='Pipeline candidate retired'):
        publish(path, tmp_path)
