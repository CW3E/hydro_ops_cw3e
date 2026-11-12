from datetime import date
from pathlib import Path

import pytest
from test_nwm_forcing_coordinator import load_script


def test_baseline_reuse_is_opt_in_and_batch_scoped(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('run_bounded_revision_queue.py')
    old = [tmp_path/'101', tmp_path/'102']
    command = module.replay_command(tmp_path, 'nrt', '2026-10-04', '103',
                                    previous_workspaces=old)
    assert '--baseline-reuse-root' not in command
    command = module.replay_command(tmp_path, 'nrt', '2026-10-04', '103',
                                    previous_workspaces=old, reuse_baselines=True)
    roots = [command[i+1] for i, v in enumerate(command) if v == '--baseline-reuse-root']
    assert roots == [str(old[1]/'baseline'), str(old[0]/'baseline'),
                     str(tmp_path/'forcing/outputs/conus/baseline/hourly')]
    assert command[command.index('--case')+1] == 'daily'


def test_reuse_preserves_all_candidate_and_publication_gates(monkeypatch, tmp_path):
    import json
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('run_bounded_revision_queue.py')
    directory = tmp_path/'forcing/status/prism-revisions'
    directory.mkdir(parents=True)
    events = {f'nrt/{d}': {'stream': 'nrt', 'day': d} for d in ('2026-10-03', '2026-10-04')}
    (directory/'queue.json').write_text(json.dumps({'pending': events}))
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setattr(module, 'select_routine', lambda *a: (events, []))
    monkeypatch.setattr(module, 'missing_publication_context', lambda *a, **kw: [])
    monkeypatch.setattr(module, 'plan', lambda *a: {'items': [dict(e, key=k, status='ready')
                                                          for k, e in events.items()]})
    monkeypatch.setattr(module.sys, 'argv', ['run', '--stream', 'nrt', '--routine', '--execute',
                                            '--publish', '--reuse-baselines'])
    calls = []
    monkeypatch.setattr(module.subprocess, 'run', lambda cmd, **kw: calls.append(cmd))
    module.main()
    assert len(calls) == 8
    assert calls[0] == calls[1] and calls[2] == calls[3]
    assert calls[4] == calls[5] and calls[6] == calls[7]
    previous = str(tmp_path/'forcing/work/isolated-revision-replay/12301/baseline')
    assert previous not in calls[0]
    assert previous in calls[4]
    assert '--publish' in calls[2] and '--publish' in calls[6]
    assert json.loads((directory/'queue.json').read_text())['pending'] == events


def test_selection_is_stream_date_and_cost_bounded(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('run_bounded_revision_queue.py')
    state = {'pending': {f'{s}/{d}': {'stream': s, 'day': d}
                         for s in ('nrt', 'retro')
                         for d in ('2026-03-30', '2026-03-31', '2026-04-01', '2026-04-02')}}
    result = module.select_pending(state, 'nrt', date(2026, 3, 31), date(2026, 4, 2), 2)
    assert list(result) == ['nrt/2026-03-31', 'nrt/2026-04-01']
    for limit in (0, 3):
        with pytest.raises(ValueError):
            module.select_pending(state, 'nrt', date(2026, 3, 31), date(2026, 4, 1), limit)
    with pytest.raises(ValueError):
        module.select_pending(state, 'nrt', date(2026, 3, 1), date(2026, 4, 1), 2)


def test_routine_selection_skips_blocked_and_preserves_stream(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('run_bounded_revision_queue.py')
    state = {'pending': {f'{s}/{d}': {'stream': s, 'day': d}
                         for s in ('nrt', 'retro')
                         for d in ('2026-03-01', '2026-04-30', '2026-05-01', '2026-05-02')}}
    monkeypatch.setattr(module, 'plan', lambda state, root: {'items': [{'status': 'ready_for_candidate'}]})
    monkeypatch.setattr(module, 'missing_publication_context', lambda root, stream, day, **kwargs:
                        ['missing'] if day.month == 3 else [])
    selected, deferred = module.select_routine(state, tmp_path, 'nrt', 2)
    assert list(selected) == ['nrt/2026-04-30', 'nrt/2026-05-01']
    assert deferred[0]['key'] == 'nrt/2026-03-01'
    assert len(state['pending']) == 8  # selection does not acknowledge anything
    assert module.select_routine({'pending': {}}, tmp_path, 'retro', 2) == ({}, [])
    with pytest.raises(ValueError):
        module.select_routine(state, tmp_path, 'nrt', 3)


def test_routine_context_requires_both_months_and_hourly_halos(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('run_bounded_revision_queue.py')
    missing = module.missing_publication_context(tmp_path, 'nrt', date(2026, 5, 1))
    assert any('202604.LDASIN_DOMAIN1.monthly' in p for p in missing)
    assert any('202605.LDASIN_DOMAIN1.monthly' in p for p in missing)
    assert any('/hourly/2026/04/20260430.' in p for p in missing)
    assert any('/hourly/2026/05/20260502.' in p for p in missing)
    for name in missing:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert module.missing_publication_context(tmp_path, 'nrt', date(2026, 5, 1)) == []


def test_routine_empty_queue_passes_without_publication(monkeypatch, tmp_path):
    import json
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('run_bounded_revision_queue.py')
    directory = tmp_path/'forcing/status/prism-revisions'
    directory.mkdir(parents=True)
    (directory/'queue.json').write_text('{"pending": {}}')
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setattr(module.sys, 'argv', ['run', '--stream', 'nrt', '--routine', '--execute', '--publish'])
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: pytest.fail('Empty queue executed work'))
    module.main()
    report = json.loads((tmp_path/'forcing/work/bounded-revision-queue/123/acceptance.json').read_text())
    assert report['status'] == 'passed'
    assert report['backlog_complete']
    assert report['days'] == []
