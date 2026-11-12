import pytest
from test_nwm_forcing_coordinator import load_script


def test_explicit_open_month_scope(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    m = load_script('drain_revision_backlog.py')
    state = {'pending': {f'nrt/2026-11-{d:02d}': {'stream': 'nrt', 'day': f'2026-11-{d:02d}'}
                         for d in (1, 2, 3)}}
    spec = {'stream': 'nrt', 'keys': ['nrt/2026-11-01', 'nrt/2026-11-03'],
            'defer_open_month': True, 'reuse_baselines': True}
    selected = m.explicit_selection(state, spec)
    assert list(selected) == ['nrt/2026-11-01']  # Never include an unapproved intervening event.
    cmd = m.batch_command(spec, selected)
    assert '--defer-open-month' in cmd and '--reuse-baselines' in cmd and '--routine' not in cmd
    spec['keys'] = list(state['pending'])
    assert len(m.explicit_selection(state, spec)) == 2
    with pytest.raises(ValueError, match='NRT only'):
        m.explicit_selection(state, {**spec, 'stream': 'retro'})


def test_backlog_cost_and_scope_bounds(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('drain_revision_backlog.py')
    assert module.next_action(['nrt/a'], ['nrt/a'], 0, 2) == 'run'
    assert module.next_action(['nrt/a'], [], 1, 2) == 'deferred_or_complete'
    assert module.next_action(['nrt/a'], ['nrt/a'], 2, 2) == 'batch_limit'
    with pytest.raises(ValueError, match='outside'):
        module.next_action(['nrt/a'], ['retro/a'], 0, 2)


def test_paused_campaign_never_reads_queue_or_launches(tmp_path, monkeypatch, capsys):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('drain_revision_backlog.py')
    campaign = tmp_path/'forcing/work/revision-backlog-drain/test'
    campaign.mkdir(parents=True)
    (campaign/'PAUSED.json').write_text('{}')
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(module.sys, 'argv', ['run', '--campaign', str(campaign)])
    module.main()
    assert 'paused' in capsys.readouterr().out
    assert not (campaign/'batch-000.json').exists()


@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('pause', [False, True])
def test_successor_only_after_acknowledged_publication(tmp_path, monkeypatch, failure, pause):
    import json
    import subprocess
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('drain_revision_backlog.py')
    campaign = tmp_path/'forcing/work/revision-backlog-drain/test'
    campaign.mkdir(parents=True)
    (campaign/'plan.json').write_text(json.dumps({'stream': 'nrt', 'keys': ['nrt/a'], 'maximum_batches': 1}))
    queue = tmp_path/'forcing/status/prism-revisions/queue.json'
    queue.parent.mkdir(parents=True)
    queue.write_text(json.dumps({'pending': {'nrt/a': {}}}))
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setattr(module.sys, 'argv', ['run', '--campaign', str(campaign)])
    monkeypatch.setattr(module, 'select_routine', lambda *a: ({'nrt/a': {}}, []))
    launches = []
    def run(*a, **kw):
        if failure:
            raise subprocess.CalledProcessError(1, ['worker'])
        queue.write_text('{"pending":{}}')
        receipt = tmp_path/'forcing/work/bounded-revision-queue/123/acceptance.json'
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({'status': 'passed', 'days': [{'key': 'nrt/a'}]}))
        if pause:
            (campaign/'PAUSED.json').write_text('{}')
    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setattr(module.subprocess, 'check_output', lambda cmd, **kw: launches.append(cmd) or '124')
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            module.main()
        assert not launches
    else:
        module.main()
        if pause:
            assert not launches
        else:
            assert len(launches) == 1 and '--dependency=afterok:123' in launches[0]
    record = json.loads((campaign/'batch-000.json').read_text())
    assert record['status'] == ('failed' if failure else 'passed')
