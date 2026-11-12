import json
from pathlib import Path

import pytest
from test_nwm_forcing_coordinator import load_script


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    return load_script('refresh_thermal_model_2026.py'), load_script('run_cnrfc_production.py')


def test_intervals_and_predecessor(modules):
    task,_ = modules
    start,end = task.interval('retro')
    assert (end-start).days == 76
    assert end == task.interval('nrt')[0]
    assert (task.interval('nrt')[1]-end).days == 204
    r = {'status':'complete','domain':'yampa','stream':'retro','end':str(end),'plan_sha256':'pin'}
    task.validate_predecessor(r,'yampa','pin')
    for key,value in [('status','failed'),('domain','b120'),('stream','nrt'),('plan_sha256','other'),('end','2026-04-17 00:00:00')]:
        with pytest.raises(ValueError,match='predecessor'):
            task.validate_predecessor(r | {key:value},'yampa','pin')


@pytest.mark.parametrize('domain',['yampa','b120','ucrb'])
@pytest.mark.parametrize('stream',['retro','nrt'])
def test_isolation_and_required_refresh_gate(tmp_path,modules,domain,stream):
    task,runner = modules
    out,rst = runner.publication_roots(tmp_path,task.CAMPAIGN,stream,domain)
    assert out.name == task.CAMPAIGN and rst.name == task.CAMPAIGN
    start,end = task.interval(stream)
    with pytest.raises(ValueError,match='locked staging'):
        runner.run(tmp_path,2026,campaign=task.CAMPAIGN,stream=stream,domain=domain,
                   start_date=str(start.date()),end_date=str(end.date()))
    gate=tmp_path/'gate.json'
    gate.write_text(json.dumps({'status':'failed'}))
    with pytest.raises(ValueError,match='bounded thermal refresh gate'):
        runner.run(tmp_path,2026,campaign=task.CAMPAIGN,stream=stream,domain=domain,
                   start_date=str(start.date()),end_date=str(end.date()),archive_gate=gate,
                   refresh_workspace=tmp_path/'stage',stream_lock=object(),
                   forcing_root=tmp_path/'forcing',initial_restart_dir=tmp_path/'initial')


def test_old_campaign_cannot_use_refresh_workspace(tmp_path,modules):
    _,runner=modules
    with pytest.raises(ValueError,match='explicit refresh campaign'):
        runner.run(tmp_path,1979,campaign='repaired_rain_retro_v1',domain='yampa',refresh_workspace=tmp_path)


def test_cnrfc_handoff_and_bounds(tmp_path, modules):
    task, runner = modules
    handoff = load_script('run_cnrfc_repaired_nrt.py')
    start, end = task.interval('nrt', 'cnrfc')
    assert str(start) == '2026-04-17 00:00:00'
    assert str(end) == '2026-11-11 00:00:00'
    previous = {'passed': True, 'stream': 'retro', 'start': '2026-01-01 00:00:00',
                'end': str(start)}
    handoff.validate_retro(previous)
    for key, value in [('passed', False), ('stream', 'nrt'), ('end', '2026-04-18 00:00:00')]:
        with pytest.raises(ValueError, match='terminal acceptance'):
            handoff.validate_retro(previous | {key: value})
    proof = {'status': 'complete', 'domain': 'cnrfc', 'stream': 'retro',
             'end': str(start), 'plan_sha256': 'pin'}
    task.validate_predecessor(proof, 'cnrfc', 'pin')
    with pytest.raises(ValueError, match='predecessor'):
        task.validate_predecessor(proof | {'end': '2026-04-18 00:00:00'}, 'cnrfc', 'pin')
    with pytest.raises(ValueError, match='existing Retro'):
        task.interval('retro', 'cnrfc')
    with pytest.raises(ValueError, match='domain/stream'):
        runner.publication_roots(tmp_path, task.CAMPAIGN, 'retro', 'cnrfc')
    assert runner.publication_roots(tmp_path, task.CAMPAIGN, 'nrt', 'cnrfc')[0].name == task.CAMPAIGN
    gate = tmp_path/'gate.json'
    gate.write_text(json.dumps({'status': 'passed', 'domain': 'cnrfc', 'stream': 'nrt',
        'start': str(start), 'end': str(end), 'restart_equivalence': 'passed',
        'forcing_publication_job': '5232861'}))
    # Correct interval reaches the separate archive gate; a shifted interval
    # must fail earlier, even with a matching-looking but unauthorized gate.
    with pytest.raises(ValueError, match='Archive recovery acceptance'):
        runner.run(tmp_path, 2026, campaign=task.CAMPAIGN, stream='nrt', domain='cnrfc',
            start_date='2026-04-17', end_date='2026-11-11', archive_gate=gate,
            refresh_workspace=tmp_path/'stage', stream_lock=object(),
            forcing_root=tmp_path/'forcing', initial_restart_dir=tmp_path/'initial')
    with pytest.raises(ValueError, match='bounded thermal refresh gate'):
        runner.run(tmp_path, 2026, campaign=task.CAMPAIGN, stream='nrt', domain='cnrfc',
            start_date='2026-04-18', end_date='2026-11-11', archive_gate=gate,
            refresh_workspace=tmp_path/'stage', stream_lock=object(),
            forcing_root=tmp_path/'forcing', initial_restart_dir=tmp_path/'initial')


def test_superseded_partial_checkpoint_retired_not_initial(tmp_path,modules):
    task,_ = modules
    from datetime import datetime
    root,staged=tmp_path/'old',tmp_path/'new'
    for day in ('2026-02-01','2026-03-01','2026-04-17'):
        stamp=datetime.fromisoformat(day)
        for name in task.restart_names(stamp):
            p=root/stamp.strftime('%Y/%m')/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.touch()
            if day=='2026-03-01':
                p=staged/stamp.strftime('%Y/%m')/name
                p.parent.mkdir(parents=True,exist_ok=True)
                p.touch()
    found=task.superseded_checkpoints(root,staged,*task.interval('retro'))
    assert len(found)==2
    assert all('20260417' in p.name or '2026-04-17' in p.name for p in found)
