# Execute only locally parsed controller AST blocks with mocked submissions.
# ruff: noqa: S102
import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydro_ops.forcing import cnrfc_sync


@pytest.mark.parametrize('cycle,frequency', [('six-hourly', 'all'), ('daily', 'hourly')])
def test_latest_cycle_followup_scope(cycle, frequency):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'bin/update_nwm_forcing.py').read_text())
    block = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                 and 'cnrfc_hourly_job_id' in ast.unparse(node))
    calls = []
    plan = {}
    namespace = {'args': SimpleNamespace(cycle=cycle), 'plan': plan, 'job': '123',
                     'settings': SimpleNamespace(project_root=root, slurm_partition='compute', slurm_account=''),
                     'submit_cnrfc': lambda *a, **kw: calls.append(kw) or '456'}
    exec(compile(ast.Module(body=block.body, type_ignores=[]), '<followup>', 'exec'), namespace)
    assert calls == [{'stream': 'nrt', 'frequency': frequency, 'after': '123', 'partition': 'compute', 'account': ''}]
    assert plan['cnrfc_hourly_job_id'] == '456'


@pytest.mark.parametrize('cycle,stream,summary,expected', [
    ('daily', 'nrt', '234', 'nrt'),
    ('monthly-retro', 'retro', None, 'retro'),
])
def test_completed_cycle_followup_scope(cycle, stream, summary, expected):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'slurm/converge_nwm_forcing_cycle.py').read_text())
    block = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                 and 'cnrfc_sync_job_id' in ast.unparse(node))
    calls = []
    state = {'cycle': cycle, 'stream': stream, 'summary_refresh_job_id': summary, 'partition': 'compute'}
    namespace = {'state': state, 'project': root, 'os': SimpleNamespace(environ={'SLURM_JOB_ID': '123'}),
                     'downstream_after': '345',
                     'submit_cnrfc': lambda *a, **kw: calls.append(kw) or '456'}
    exec(compile(ast.Module(body=block.body, type_ignores=[]), '<followup>', 'exec'), namespace)
    assert calls == [{'stream': expected, 'frequency': 'all', 'after': '345',
                         'partition': 'compute', 'account': '', 'require_success': True}]
    assert state['cnrfc_sync_job_id'] == '456'


def test_serialized_submission(tmp_path, monkeypatch):
    monkeypatch.setattr('hydro_ops.config.load_settings', lambda: SimpleNamespace(slurm_partition='shared-128', slurm_account=''))
    calls = []
    def fake(args, **kwargs):
        calls.append(args)
        return '123|forcing-cnrfc-sync-nrt-hourly\n42|unrelated\n' if args[0] == 'squeue' else '456\n'
    monkeypatch.setattr(cnrfc_sync.subprocess, 'check_output', fake)
    assert cnrfc_sync.submit(tmp_path, after='234') == '456'
    assert '--dependency=afterany:123:234' in calls[-1]
    assert (tmp_path / 'forcing/status/cnrfc/submission-456.json').exists()
    assert cnrfc_sync.submit(tmp_path, after='234', require_success=True) == '456'
    assert '--dependency=afterok:234,afterany:123' in calls[-1]


def test_retro_catchup_does_not_gate_nrt_model(monkeypatch):
    import subprocess

    from hydro_ops.wrf_hydro import nrt_schedule

    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'slurm/converge_nwm_forcing_cycle.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    model = next(n for n in function.body if isinstance(n, ast.If)
                 and 'cnrfc_model_cycle' in ast.unparse(n))
    retro = next(n for n in function.body if isinstance(n, ast.If)
                 and 'cnrfc_retro_sync_job_id' in ast.unparse(n))
    calls = []
    monkeypatch.setattr(nrt_schedule, 'submit_if_enabled',
                        lambda root, deps: calls.append(list(deps)) or {'job_id': '777'})
    state = {'cycle': 'daily', 'stream': 'nrt', 'cnrfc_sync_job_id': '456',
                 'summary_refresh_job_id': '234', 'partition': 'shared-128'}
    namespace = {'state': state, 'project': root, 'os': SimpleNamespace(environ={'SLURM_JOB_ID': '123'}),
                     'downstream_after': '345',
                     'subprocess': subprocess}
    # Execute the successful model-submission body (not its enclosing exception return).
    exec(compile(ast.Module(body=model.body[:1] + model.body[1].body, type_ignores=[]),
                 '<model-followup>', 'exec'), namespace)
    assert calls == [['456', '345', '123']]
    def fail(*args, **kwargs):
        assert kwargs['stream'] == 'retro' and kwargs['after'] == '456'
        raise RuntimeError('simulated historical submission failure')
    namespace['submit_cnrfc'] = fail
    exec(compile(ast.Module(body=[retro], type_ignores=[]), '<retro-followup>', 'exec'), namespace)
    assert state['cnrfc_model_cycle'] == {'job_id': '777'}
    assert 'simulated' in state['cnrfc_retro_sync_submission_error']


def test_forcing_only_cycle_suppresses_all_model_branches(monkeypatch):
    import subprocess

    from hydro_ops.forcing import subdomain_sync
    from hydro_ops.wrf_hydro import nrt_schedule

    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root/'slurm/converge_nwm_forcing_cycle.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    blocks = [n for n in function.body if isinstance(n, ast.If)
              and ('cnrfc_model_cycle' in ast.unparse(n) or 'subdomain_model_cycles' in ast.unparse(n))]
    assert len(blocks) == 2
    monkeypatch.setattr(nrt_schedule, 'submit_if_enabled', lambda *a, **kw: pytest.fail('Model submitted'))
    monkeypatch.setattr(subdomain_sync, 'submit_daily_branches',
                        lambda *a, **kw: {'b120': '501', 'ucrb': '502', 'yampa': '503'})
    state = {'cycle': 'daily', 'stream': 'nrt', 'skip_model_runs': True,
             'cnrfc_sync_job_id': '456', 'partition': 'shared-128'}
    namespace = {'state': state, 'project': root, 'downstream_after': '345',
                 'os': SimpleNamespace(environ={'SLURM_JOB_ID': '123'}), 'subprocess': subprocess}
    function.body = blocks
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<forcing-only>', 'exec'), namespace)
    namespace['main']()
    assert 'cnrfc_model_cycle' not in state
    assert all(v['status'] == 'disabled_for_forcing_cycle_test' for v in state['subdomain_model_cycles'].values())


def test_daily_ucrb_dependency_and_independent_model_failure(monkeypatch):
    import subprocess

    from hydro_ops.forcing import subdomain_sync
    from hydro_ops.wrf_hydro import nrt_schedule

    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root/'slurm/converge_nwm_forcing_cycle.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    block = next(n for n in function.body if isinstance(n, ast.If)
                 and 'subdomain_model_cycles' in ast.unparse(n))
    monkeypatch.setattr(subdomain_sync, 'submit_daily_branches',
                        lambda *a, **kw: {'b120': '501', 'ucrb': '502', 'yampa': '503'})
    calls = {}
    def submit(root, dependencies, domain):
        calls[domain] = dependencies
        if domain == 'ucrb':
            raise ValueError('UCRB gate not yet accepted')
        return {'status': 'submitted'}
    monkeypatch.setattr(nrt_schedule, 'submit_if_enabled', submit)
    state = {'cycle': 'daily', 'stream': 'nrt', 'cnrfc_sync_job_id': '456',
             'partition': 'shared-128'}
    namespace = {'state': state, 'project': root, 'downstream_after': '345',
                 'os': SimpleNamespace(environ={'SLURM_JOB_ID': '123'}),
                 'subprocess': subprocess, 'submit_if_enabled': submit}
    function.body = [block]
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<daily-domains>', 'exec'), namespace)
    namespace['main']()
    assert calls == {'b120': ['501', '345', '123'], 'ucrb': ['502', '345', '123'],
                     'yampa': ['503', '345', '123']}
    assert 'ucrb' in state['subdomain_model_submission_errors']
    assert state['subdomain_model_cycles']['yampa']['status'] == 'submitted'


def test_incremental_skip_is_metadata_only(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('backfill_cnrfc_forcing')
    source, target, mask = [tmp_path / n for n in ('source', 'target', 'mask')]
    source.write_bytes(b'original'); mask.write_bytes(b'mask')
    def subset(src, dst, masks):
        dst.write_bytes(src.read_bytes())
        return {'status': 'passed', 'source': str(src.resolve())}
    monkeypatch.setattr(tool, 'subset', subset)
    task = (source, target, mask, tool.digest(mask), tmp_path, tool.identity(source))
    assert tool.process(task)['status'] == 'published'
    original = tool.digest
    def digest(path):
        assert path != target, 'Unchanged output must not be rehashed'
        return original(path)
    monkeypatch.setattr(tool, 'digest', digest)
    assert tool.process(task)['status'] == 'skipped'
    source.write_bytes(b'new value')
    monkeypatch.setattr(tool, 'digest', original)
    assert tool.process_retry(task)['status'] == 'published'
    assert target.read_bytes() == b'new value'


def test_stream_resolution_filters(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('backfill_cnrfc_forcing')
    for stream in ('nrt', 'retro'):
        for suffix in ('hourly/2026/09/20260925.LDASIN_DOMAIN1',
                       'daily/2026/09/20260925.LDASIN_DOMAIN1.daily',
                       'monthly/2026/202609.LDASIN_DOMAIN1.monthly'):
            p = tmp_path / stream / suffix
            p.parent.mkdir(parents=True, exist_ok=True); p.touch()
    assert len(tool.discover(tmp_path)) == 6
    assert len(tool.discover(tmp_path, ('nrt',), ('hourly',))) == 1
