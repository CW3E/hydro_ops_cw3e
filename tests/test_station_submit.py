import json

import pytest

from hydro_ops.observations import station_submit as mod
from hydro_ops.observations.station_operations import COLLECTION, STATE, active_catalog


def activate(root):
    state = root/STATE
    state.mkdir(parents=True)
    (state/'active.json').write_text(json.dumps({'status': 'active', 'catalog': COLLECTION+'/catalog.json'}))


def test_dry_run_serializes_and_duplicate_skips(tmp_path, monkeypatch):
    activate(tmp_path)
    monkeypatch.setattr(mod.subprocess, 'check_output', lambda *a, **k: '123|obs-stations-cdec-swe\n')
    result = mod.submit(tmp_path, 'cdec-fnf', dry_run=True)
    assert '--dependency=afterany:123' in result['command']
    assert mod.submit(tmp_path, 'cdec-swe')['status'] == 'skipped'


def test_incomplete_cutover_blocks_routing(tmp_path):
    activate(tmp_path)
    (tmp_path/STATE/'active.json').write_text(json.dumps({'status': 'preparing'}))
    with pytest.raises(ValueError, match='incomplete'):
        active_catalog(tmp_path)


def test_uncertain_submission_not_repeated(tmp_path, monkeypatch):
    activate(tmp_path)
    monkeypatch.setattr(mod.subprocess, 'check_output', lambda *a, **k: '')
    (tmp_path/STATE/'usgs-submission.json').write_text(json.dumps({'status': 'submission_uncertain'}))
    with pytest.raises(ValueError, match='uncertain'):
        mod.submit(tmp_path, 'usgs')


def test_bounded_continuation_follows_current_job(tmp_path, monkeypatch):
    activate(tmp_path)
    monkeypatch.setenv('SLURM_JOB_ID','123')
    monkeypatch.setattr(mod.subprocess,'check_output',lambda *a,**k:'123|obs-stations-usgs\n')
    report = tmp_path/'observations/work/station-cycles/123/acceptance.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'selection':'usgs'}))
    result = mod.submit(tmp_path,'usgs',dry_run=True,resume_report=report,continuations=2,after_job='123')
    assert result['status'] == 'planned'
    assert '--dependency=afterany:123' in result['command']
    assert result['command'][-2:] == ['--continuations','2']
    with pytest.raises(ValueError,match='current SLURM'):
        mod.submit(tmp_path,'usgs',after_job='999')
