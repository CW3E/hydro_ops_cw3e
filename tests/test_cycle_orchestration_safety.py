import fcntl
from datetime import date
from types import SimpleNamespace

import pytest
from test_nwm_forcing_coordinator import load_script


@pytest.mark.parametrize('cycle,stream', [('daily', 'nrt'), ('monthly-retro', 'retro')])
def test_bounded_canary_routes_only_one_publisher(tmp_path, monkeypatch, cycle, stream):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    import publish_bounded_prism_replay
    monkeypatch.setattr(publish_bounded_prism_replay, 'bounded_plan', lambda *a: {'scope': 'bounded'})
    module = load_script('update_nwm_forcing.py')
    settings = SimpleNamespace(project_root=tmp_path, work_root=tmp_path,
                               slurm_partition='shared-128', slurm_account='')
    args = SimpleNamespace(cycle=cycle, replay_workspace=tmp_path/'candidate',
                           replay_day=date(2026, 4, 12), dry_run=True)
    calls = []
    monkeypatch.setattr(module.subprocess, 'check_output', lambda c, **kw: calls.append((c, kw)) or '123\n')
    module.bounded_replay(settings, args, stream)
    assert not calls
    args.dry_run = False
    monkeypatch.setenv('SBATCH_RESERVATION', 'forbidden')
    module.bounded_replay(settings, args, stream)
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert 'slurm/publish_bounded_prism_replay.sh' in command
    assert command[command.index('--stream')+1] == stream
    assert 'SBATCH_RESERVATION' not in kwargs['env']


def harness(tmp_path, monkeypatch, cycle, active=''):
    mod = load_script('update_nwm_forcing.py')
    settings = SimpleNamespace(project_root=tmp_path, work_root=tmp_path/'work',
                               log_root=tmp_path/'logs', slurm_partition='shared-128', slurm_account='')
    calls = []
    monkeypatch.setattr(mod, 'load_settings', lambda: settings)
    monkeypatch.setattr(mod, 'require_operational_gfs', lambda root: None)
    monkeypatch.setattr(mod, 'configuration', lambda root: {'enabled': True, 'lookback_days': 11})
    monkeypatch.setattr(mod, 'activation', lambda root: True)
    monkeypatch.setattr(mod, 'retro_backlog', lambda *args: [])
    monkeypatch.setattr(mod.sys, 'argv', ['update_nwm_forcing.py', '--cycle', cycle])
    def run(command, **kwargs):
        calls.append(command)
        stdout = active if command[0] == 'squeue' else ''
        if 'bin/update_forcing.py' in command:
            stdout = 'Submitted batch job 101\nSubmitted batch job 102\n'
        if 'bin/submit_forcing_days.py' in command:
            stdout = 'Submitted batch job 103\n'
        return SimpleNamespace(stdout=stdout, stderr='', returncode=0)
    def output(command, **kwargs):
        calls.append(command)
        return '1001\n'
    monkeypatch.setattr(mod.subprocess, 'run', run)
    monkeypatch.setattr(mod.subprocess, 'check_output', output)
    def sync(root, **kwargs):
        calls.append(['subset-sync', kwargs])
        return '1002'
    monkeypatch.setattr('hydro_ops.forcing.cnrfc_sync.submit', sync)
    return mod, settings, calls


def test_daily_latest_precedes_revisions_and_subsets(tmp_path, monkeypatch):
    mod, _, calls = harness(tmp_path, monkeypatch, 'daily')
    assert mod.main() == 0
    submissions = [c for c in calls if c[0] == 'sbatch']
    assert len(submissions) == 2
    assert '--job-name=nwm-cycle-daily-latest-hour' in submissions[0]
    assert '--dependency=afterok:1001' in submissions[1]
    assert '--revisions-only' in submissions[1][-1]
    assert calls[-1] == ['subset-sync', {'stream': 'nrt', 'frequency': 'hourly',
                                       'after': '1001', 'partition': 'shared-128', 'account': ''}]


def test_forcing_only_daily_propagates_model_suppression(tmp_path, monkeypatch):
    import json
    mod, settings, calls = harness(tmp_path, monkeypatch, 'daily')
    monkeypatch.setattr(mod.sys, 'argv', ['update_nwm_forcing.py', '--cycle', 'daily', '--skip-model-runs'])
    assert mod.main() == 0
    submissions = [c for c in calls if c[0] == 'sbatch']
    assert '--skip-model-runs' in submissions[1][-1]
    manifest = json.loads((settings.work_root/'nwm-latest-cycle-daily-1001.json').read_text())
    assert manifest['skip_model_runs'] is True


def test_monthly_refresh_queue_baseline_and_continuation_dependencies(tmp_path, monkeypatch):
    mod, _, calls = harness(tmp_path, monkeypatch, 'monthly-retro')
    assert mod.main() == 0
    refresh = next(i for i, c in enumerate(calls) if 'bin/update_forcing.py' in c)
    queue = next(i for i, c in enumerate(calls) if 'slurm/queue_prism_revisions.sh' in c)
    baseline = next(i for i, c in enumerate(calls) if 'bin/submit_forcing_days.py' in c)
    continuation = next(c for c in calls if 'slurm/converge_nwm_forcing_cycle.py' in c)
    assert refresh < queue < baseline
    assert '--dependency=afterany:101:102' in calls[queue]
    assert 'afterany:101:102' in calls[baseline]
    assert '--dependency=afterany:101:102:103' in continuation


@pytest.mark.parametrize('cycle,active', [
    ('daily', '101|nwm-cycle-six-hourly-latest-hour'),
    ('six-hourly', '101|nwm-cycle-daily-latest-hour'),
    ('monthly-retro', '101|prism-retro-calendar-batches'),
])
def test_same_stream_overlap_skips_submission(tmp_path, monkeypatch, cycle, active):
    mod, _, calls = harness(tmp_path, monkeypatch, cycle, active)
    assert mod.main() == 0
    assert len(calls) == 1 and calls[0][0] == 'squeue'


def test_submission_lock_blocks_other_cycle(tmp_path, monkeypatch):
    mod, settings, calls = harness(tmp_path, monkeypatch, 'monthly-retro')
    settings.work_root.mkdir()
    with (settings.work_root/'update_nwm_forcing.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert mod.main() == 1
    assert calls == []
