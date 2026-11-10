import fcntl

import pytest

from hydro_ops.forcing.prism_replay_execution import run_stage


def test_interruption_and_accepted_retry(tmp_path):
    output = tmp_path/'artifact'
    calls = []
    def interrupted():
        output.write_text('accepted by worker')
        raise RuntimeError('interrupt after write')
    with pytest.raises(RuntimeError):
        run_stage(tmp_path, 'baseline', 'v1', interrupted)
    def action():
        calls.append(1)
        return {'status': 'passed', 'artifacts': [output]}
    assert not run_stage(tmp_path, 'baseline', 'v1', action)['reused']
    assert run_stage(tmp_path, 'baseline', 'v1', action)['reused']
    assert len(calls) == 1
    with pytest.raises(ValueError, match='signature'):
        run_stage(tmp_path, 'baseline', 'v2', action)
    output.write_text('changed')
    with pytest.raises(ValueError, match='artifact changed'):
        run_stage(tmp_path, 'baseline', 'v1', action)


def test_reject_unaccepted_or_external(tmp_path):
    with pytest.raises(ValueError, match='accepted artifacts'):
        run_stage(tmp_path, 'bad', 'v1', lambda: {'status': 'failed'})
    with pytest.raises(ValueError, match='outside'):
        run_stage(tmp_path, 'external', 'v1', lambda: {
            'status': 'passed', 'artifacts': [tmp_path.parent/'external']})


def test_overlapping_stage_worker_rejected(tmp_path):
    called = []
    with (tmp_path/'stages.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            run_stage(tmp_path, 'baseline', 'v1', lambda: called.append(True))
    assert called == []
