from pathlib import Path

import pytest
from test_nwm_forcing_coordinator import load_script


@pytest.fixture
def replay(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    return load_script('test_isolated_revision_replay.py')


@pytest.mark.parametrize('stream', ['nrt', 'retro'])
def test_paths_pass_stream_guard_before_processing(replay, tmp_path, stream):
    work, windows, calendar = replay.replay_paths(tmp_path, '123', stream)
    assert work == tmp_path/'forcing/work/isolated-revision-replay/123'
    assert windows == work/'windows'/stream/'hourly'
    assert calendar == work/'calendar'/stream/'hourly'
    for path in (windows, calendar):
        assert replay.validate_stream_output_root(path, stream) == path


def test_resume_preserves_workspace_and_rejects_missing_checkpoint(replay, tmp_path):
    with pytest.raises(ValueError, match='checkpoint'):
        replay.replay_paths(tmp_path, '456', 'retro', '123')
    work = tmp_path/'forcing/work/isolated-revision-replay/123'
    work.mkdir(parents=True)
    for name in ('context.json', 'stages.json', 'queue.json', 'native-pins.json'):
        (work/name).write_text('{}')
    assert replay.replay_paths(tmp_path, '456', 'retro', '123')[0] == work


@pytest.mark.parametrize('job', ['../production', '/tmp/123', ''])
def test_reject_unsafe_job_ids(replay, tmp_path, job):
    with pytest.raises(ValueError, match='numeric'):
        replay.replay_paths(tmp_path, job, 'nrt')


def test_reject_workspace_symlink_escape(replay, tmp_path):
    base = tmp_path/'forcing/work/isolated-revision-replay'
    base.mkdir(parents=True)
    (base/'123').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='escapes'):
        replay.replay_paths(tmp_path, '123', 'retro')
