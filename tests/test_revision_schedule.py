import pytest

from hydro_ops.forcing import revision_schedule


@pytest.mark.parametrize('cycle,stream', [('daily', 'nrt'), ('monthly-retro', 'retro')])
def test_schedule_orders_scan_replay_and_sanitizes_reservation(monkeypatch, tmp_path, cycle, stream):
    calls = []
    def output(command, **kwargs):
        calls.append((command, kwargs))
        return str(100+len(calls))+';cluster\n'
    monkeypatch.setattr(revision_schedule.subprocess, 'check_output', output)
    monkeypatch.setenv('SBATCH_RESERVATION', 'NRTRES')
    result = revision_schedule.submit(tmp_path, cycle=cycle, stream=stream,
                                      partition='shared-128', account='test', dependencies=['90', '91'])
    assert result['replay_job_id'] == '102'
    assert '--dependency=afterok:90:91' in calls[0][0]
    assert '--dependency=afterok:101' in calls[1][0]
    assert calls[1][0][-4:] == ['--limit', '2', '--execute', '--publish']
    assert '--routine' in calls[1][0]
    assert calls[1][0][calls[1][0].index('--stream')+1] == stream
    assert all('SBATCH_RESERVATION' not in kwargs['env'] for _, kwargs in calls)


def test_no_six_hourly_or_unordered_replay():
    with pytest.raises(ValueError):
        revision_schedule.commands('six-hourly', 'nrt', 'shared-128', '', ['1'])
    with pytest.raises(ValueError):
        revision_schedule.commands('daily', 'nrt', 'shared-128', '', [])


def test_retro_continuation_subsets_wait_for_replay(tmp_path, monkeypatch):
    import json

    from test_nwm_forcing_coordinator import load_slurm_script
    module = load_slurm_script('converge_nwm_forcing_cycle.py')
    manifest = tmp_path/'cycle.json'
    manifest.write_text(json.dumps({'start': '2026-03-01', 'end': '2026-03-02',
                                   'stream': 'retro', 'cycle': 'monthly-retro',
                                   'partition': 'shared-128', 'cleanup_mode': 'deferred'}))
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('HYDRO_OPS_CYCLE_MANIFEST', str(manifest))
    monkeypatch.setenv('SLURM_JOB_ID', '99')
    monkeypatch.setattr(module, 'unresolved_days', lambda *a: [])
    calls = []
    def replay(root, **kwargs):
        calls.append(('replay', kwargs))
        return {'scan_job_id': '100', 'replay_job_id': '101'}
    def subset(root, **kwargs):
        calls.append(('subset', kwargs))
        return '102'
    monkeypatch.setattr(revision_schedule, 'submit', replay)
    monkeypatch.setattr('hydro_ops.forcing.cnrfc_sync.submit', subset)
    assert module.main() == 0
    assert calls[0][1]['dependencies'] == ['99']
    assert calls[1][1]['after'] == '101'
    assert json.loads(manifest.read_text())['status'] == 'normal_publication_complete_revision_replay_submitted'
