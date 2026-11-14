import json

import pytest

from hydro_ops.web.globus_transfer import atomic_json
from hydro_ops.web.schedule import jobs_from_cycle, submit_if_enabled


def state():
    return {'cycle': 'daily', 'stream': 'nrt', 'cnrfc_sync_job_id': '11',
            'cnrfc_model_cycle': {'status': 'submitted', 'job_id': '12'},
            'subdomain_sync_jobs': {'ucrb': '13'},
            'subdomain_model_cycles': {'ucrb': {'status': 'submitted', 'job_id': '14'}}}


def test_exact_cycle_models_required():
    assert jobs_from_cycle(state())['ucrb'] == {'forcing': '13', 'model': '14'}
    for status in ('already_submitted', 'disabled', 'deferred_model_job_active'):
        s = state()
        s['cnrfc_model_cycle']['status'] = status
        with pytest.raises(ValueError, match='this cycle'):
            jobs_from_cycle(s)
    with pytest.raises(ValueError, match='disabled'):
        jobs_from_cycle(dict(state(), skip_model_runs=True))


def test_disabled_and_dependency_submission(tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    path = tmp_path / 'config/web_daily.json'
    atomic_json(path, {'enabled': False, 'mode': 'plan-only'})
    calls = []

    def submit(command, **kwargs):
        calls.append(command)
        return '999\n'

    monkeypatch.setattr('hydro_ops.web.schedule.subprocess.check_output', submit)
    assert submit_if_enabled(tmp_path, state(), '10')['status'] == 'disabled'
    assert not calls
    atomic_json(path, {'enabled': True, 'mode': 'plan-only'})
    assert submit_if_enabled(tmp_path, state(), '10')['job_id'] == '999'
    assert '--dependency=afterok:10:11:12:13:14' in calls[0]
    assert '--kill-on-invalid-dep=yes' in calls[0]
    assert not any('reservation' in v for v in calls[0])
    assert submit_if_enabled(tmp_path, state(), '10')['status'] == 'already_recorded'
    assert len(calls) == 1
    request = tmp_path / 'web/status/daily/submissions/10/jobs.json'
    assert json.loads(request.read_text()) == jobs_from_cycle(state())


def test_submission_uncertainty_blocks_duplicates(tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    atomic_json(tmp_path / 'config/web_daily.json', {'enabled': True, 'mode': 'plan-only'})

    def fail(*args, **kwargs):
        raise OSError('sbatch unavailable')

    monkeypatch.setattr('hydro_ops.web.schedule.subprocess.check_output', fail)
    with pytest.raises(OSError):
        submit_if_enabled(tmp_path, state(), '10')
    assert submit_if_enabled(tmp_path, state(), '10')['status'] == 'already_recorded'
