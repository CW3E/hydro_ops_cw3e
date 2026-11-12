import json

import pytest

from hydro_ops.observations import station_submit as mod
from hydro_ops.observations.station_operations import COLLECTION, STATE, active_catalog


def activate(root):
    state = root/STATE
    state.mkdir(parents=True)
    (state/'active.json').write_text(json.dumps({'status': 'active', 'catalog': COLLECTION+'/catalog.json'}))


def test_swe_resolution_policy(tmp_path, monkeypatch):
    activate(tmp_path)
    monkeypatch.setattr(mod.subprocess, 'check_output', lambda *a, **k: '')
    cmd = mod.submit(tmp_path, 'cdec-swe', dry_run=True)['command']
    assert cmd[-3:] == ['--swe-resolutions', 'daily', 'monthly']
    cmd = mod.submit(tmp_path, 'cdec-swe', dry_run=True, swe_resolutions=['hourly', 'event'])['command']
    assert cmd[-3:] == ['--swe-resolutions', 'event', 'hourly']
    with pytest.raises(ValueError, match='resolutions'):
        mod.submit(tmp_path, 'cdec-swe', dry_run=True, swe_resolutions=[])
    cmd = mod.submit(tmp_path, 'cdec-swe', dry_run=True, prepare_workers=4,
                     optimized_coordinator=True)['command']
    assert '--optimized-coordinator' in cmd
    with pytest.raises(ValueError, match='requires preparation'):
        mod.submit(tmp_path, 'cdec-swe', dry_run=True, optimized_coordinator=True)


def test_usgs_cohorts_have_distinct_jobs_and_resume_guards(tmp_path, monkeypatch):
    activate(tmp_path)
    monkeypatch.setattr(mod.subprocess, 'check_output', lambda *a, **k: '123|obs-stations-usgs-regional\n')
    assert mod.submit(tmp_path, 'usgs', usgs_scope='regional', dry_run=True)['status'] == 'skipped'
    cmd = mod.submit(tmp_path, 'usgs', usgs_scope='remainder', dry_run=True)['command']
    assert '--job-name=obs-stations-usgs-remainder' in cmd
    assert '--dependency=afterany:123' in cmd
    assert cmd[cmd.index('--usgs-scope')+1] == 'remainder'
    report = tmp_path/'observations/work/station-cycles/123/acceptance.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'selection': 'usgs', 'usgs_scope': 'regional'}))
    with pytest.raises(ValueError, match='Resume selection'):
        mod.submit(tmp_path, 'usgs', usgs_scope='remainder', resume_report=report, dry_run=True)


def test_dry_run_serializes_and_duplicate_skips(tmp_path, monkeypatch):
    activate(tmp_path)
    monkeypatch.setattr(mod.subprocess, 'check_output', lambda *a, **k: '123|obs-stations-cdec-swe\n')
    result = mod.submit(tmp_path, 'cdec-fnf', dry_run=True)
    assert '--dependency=afterany:123' in result['command']
    assert mod.submit(tmp_path, 'cdec-swe')['status'] == 'skipped'


def test_prepared_bounded_submission_is_explicit(tmp_path, monkeypatch):
    activate(tmp_path)
    monkeypatch.setattr(mod.subprocess, 'check_output', lambda *a, **k: '')
    command = mod.submit(tmp_path, 'usgs', dry_run=True, prepare_workers=4, limit=32)['command']
    assert command[-4:] == ['--prepare-workers', '4', '--limit', '32']
    default = mod.submit(tmp_path, 'usgs', dry_run=True)['command']
    assert default[-2:] == ['--prepare-workers', '4']
    assert '--prepare-workers' not in mod.submit(tmp_path, 'cdec-fnf', dry_run=True)['command']
    with pytest.raises(ValueError, match='uncommissioned'):
        mod.submit(tmp_path, 'usgs', prepare_workers=4, continuations=1)
    with pytest.raises(ValueError, match='bounded cycle'):
        mod.submit(tmp_path, 'usgs', limit=0)


def test_full_cycle_requires_successful_repeat_and_keeps_other_serialization(tmp_path, monkeypatch):
    activate(tmp_path)
    monkeypatch.setattr(mod.subprocess, 'check_output', lambda *a, **k:
                        '123|obs-stations-usgs\n124|obs-stations-cdec-swe\n')
    result = mod.submit(tmp_path, 'usgs', dry_run=True, prepare_workers=4, after_success='123')
    assert '--dependency=afterany:124,afterok:123' in result['command']
    assert '--kill-on-invalid-dep=yes' in result['command']
    assert mod.submit(tmp_path, 'usgs', dry_run=True, after_success='999')['status'] == 'skipped'
    with pytest.raises(ValueError, match='numeric job'):
        mod.submit(tmp_path, 'usgs', after_success='bad')


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
    result = mod.submit(tmp_path,'usgs',dry_run=True,resume_report=report,continuations=2,
                        after_job='123',prepare_workers=0)
    assert result['status'] == 'planned'
    assert '--dependency=afterany:123' in result['command']
    assert result['command'][-2:] == ['--continuations','2']
    with pytest.raises(ValueError,match='current SLURM'):
        mod.submit(tmp_path,'usgs',after_job='999')
