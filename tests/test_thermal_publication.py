import json
from datetime import UTC, date, datetime

import numpy as np
import pytest
from netCDF4 import Dataset
from test_nwm_forcing_coordinator import load_script


def acceptance():
    return {'status':'passed_candidate_only','window':'2026-04-12',
            'changed_variables':['T2D','Q2D','LWDOWN'],
            'files':[{'stream':s,'day':d,'changed_hours':hours}
                     for s in ('retro','nrt')
                     for d,hours in [('2026-04-11',list(range(12,24))),('2026-04-12',list(range(12)))]]}


def test_scope_requires_both_halves_and_streams(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('publish_temperature_refresh_trial.py')
    days,daily = module.scope(acceptance())
    assert list(map(str,days)) == ['2026-04-11','2026-04-12']
    assert list(map(str,daily)) == ['2026-04-10','2026-04-11','2026-04-12']
    bad = acceptance()
    bad['files'].pop()
    with pytest.raises(ValueError,match='both calendar halves'):
        module.scope(bad)
    bad = acceptance()
    bad['files'][0]['changed_hours'] = list(range(24))
    with pytest.raises(ValueError,match='changed-hour'):
        module.scope(bad)


def test_summary_preserves_rain(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('publish_temperature_refresh_trial.py')
    paths = [tmp_path/'a.nc',tmp_path/'b.nc']
    for path in paths:
        with Dataset(path,'w') as ds:
            ds.createDimension('y',2)
            ds.createDimension('x',2)
            for name in ('RAINRATE','T2D'):
                ds.createVariable(name,'f4',('y','x'))[:] = np.ones((2,2))
    with Dataset(paths[1],'r+') as ds:
        ds['T2D'][:] = 3
    module.verify_summary_preservation(*paths)
    with Dataset(paths[1],'r+') as ds:
        ds['RAINRATE'][0,0] = 4
    with pytest.raises(AssertionError):
        module.verify_summary_preservation(*paths)


def test_absent_incomplete_month_deferred_not_fabricated(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('publish_temperature_refresh_trial.py')
    month = date(2026,4,1)
    ready,deferred = module.monthly_scope(tmp_path,'retro',[month])
    assert ready == [] and len(deferred[0]['missing_daily_context']) == 30
    path = module.summary_path(tmp_path/'forcing/outputs/conus/retro/monthly',month,monthly=True)
    path.parent.mkdir(parents=True)
    path.touch()
    with pytest.raises(ValueError,match='Existing monthly product'):
        module.monthly_scope(tmp_path,'retro',[month])


def test_model_acceptance_needs_completed_downstream(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('extend_repaired_retro.py')
    path = tmp_path/'forcing/outputs/conus/retro/hourly/2026/04/20260411.LDASIN_DOMAIN1'
    path.parent.mkdir(parents=True)
    publication = tmp_path/'forcing/work/temperature-refresh/1/publication/acceptance.json'
    publication.parent.mkdir(parents=True)
    publication.write_text(json.dumps({'status':'passed','rain_preserved':True,
                                     'published_artifacts':{str(path):[1,2,3]}}))
    manifest = {'verification':'accepted_thermal_refresh_static_envelope',
                'temperature_refresh':{'publication_acceptance':str(publication),
                                       'unchanged_nonthermal_payloads':True},
                'day':'2026-04-11','forcing_stream':'retro','complete_day':True,
                'derived_products_pending':True}
    meta = path.with_name(path.name+'.manifest.json')
    meta.write_text(json.dumps(manifest))
    day = datetime(2026,4,11,tzinfo=UTC)
    assert not module.accepted_replay(tmp_path,path,day,[1,2,3])
    manifest['derived_products_pending'] = False
    meta.write_text(json.dumps(manifest))
    assert module.accepted_replay(tmp_path,path,day,[1,2,3])
    assert not module.accepted_replay(tmp_path,path,day,[1,2,4])


def test_daily_boundary_deferral_and_complete_new_summary(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('publish_temperature_refresh_trial.py')
    day = date(2026,4,18)
    base = tmp_path/'forcing/outputs/conus/retro'
    def hourly(stamp, hours):
        path = base/'hourly'/stamp.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        path.parent.mkdir(parents=True,exist_ok=True)
        with Dataset(path,'w') as ds:
            ds.createDimension('time',len(hours))
            v = ds.createVariable('time','f8',('time',))
            v.units = f'hours since {stamp} 00:00:00'
            v[:] = hours
        return path
    hourly(day,range(24))
    ready,deferred = module.daily_scope(tmp_path,'retro',[day])
    assert ready == [] and deferred[0]['available_interval_records'] == 23
    summary = module.summary_path(base/'daily',day)
    assert module.target_identity(summary) is None
    summary.parent.mkdir(parents=True)
    summary.touch()
    with pytest.raises(ValueError,match='Existing daily product'):
        module.daily_scope(tmp_path,'retro',[day])
    summary.unlink()
    next_day = date(2026,4,19)
    hourly(next_day,[0])
    assert module.daily_scope(tmp_path,'retro',[day]) == ([day],[])
    hourly(next_day,[0,0])
    with pytest.raises(ValueError,match='Invalid daily interval timestamps'):
        module.daily_scope(tmp_path,'retro',[day])


def test_planned_daily_can_complete_month(tmp_path, monkeypatch):
    from datetime import timedelta
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('publish_temperature_refresh_trial.py')
    month = date(2026,4,1)
    days = [month+timedelta(days=i) for i in range(30)]
    assert module.monthly_scope(tmp_path,'retro',[month],planned_daily=days) == ([month],[])


def test_new_summary_without_preservation_reference(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('publish_temperature_refresh_trial.py')
    calls = []
    monkeypatch.setattr(module,'summarize',lambda *a,**k:calls.append(a))
    def reject(*args):
        raise AssertionError('Must not compare against nonexistent original')
    monkeypatch.setattr(module,'verify_summary_preservation',reject)
    out = tmp_path/'new.nc'
    task = (tmp_path,out,date(2026,4,1),date(2026,4,2),{}, {}, {},'retro',False,None)
    assert module.summary_task(task) == out
    assert len(calls) == 1
