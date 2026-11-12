import json
from contextlib import nullcontext
from copy import deepcopy

import pytest
import requests
from test_nwm_forcing_coordinator import load_script
from test_station_updates import sample

from hydro_ops.observations.station_updates import merge_window, retain_omitted


def test_routine_swe_resolutions_and_explicit_opt_in():
    m = load_script('update_station_collection.py')
    for resolution in ('daily', 'monthly', 'hourly', 'event'):
        entry = {'provider': 'cdec', 'product': 'snow_water_equivalent', 'resolution': resolution}
        assert m.selected(entry, 'cdec-swe') == (resolution in ('daily', 'monthly'))
        assert m.selected(entry, 'cdec-swe', [resolution])
        assert not m.selected(entry, 'cdec-fnf')
    assert m.selected({'provider': 'usgs', 'product': 'discharge', 'resolution': 'daily'}, 'usgs')


def test_omissions_preserved_but_explicit_missing_replaces():
    old,incoming = sample(),sample()
    incoming.rows.pop()
    incoming.rows[0].update(value='',flag='missing')
    restored,omitted = retain_omitted(old,incoming,start='2020-01-01',end='2020-01-03')
    assert omitted == [{'series':'a','period':'2020-01-03'}]
    result = merge_window(old,restored,start='2020-01-01',end='2020-01-03')
    assert result.rows[-1] == old.rows[-1]
    assert result.rows[0]['flag'] == 'missing'
    assert len(incoming.rows) == 2
    bad = deepcopy(incoming)
    bad.metadata['timezone'] = 'UTC'
    with pytest.raises(ValueError,match='semantics changed'):
        retain_omitted(old,bad,start='2020-01-01',end='2020-01-03')


def test_resume_only_current_successful_members(tmp_path):
    m = load_script('update_station_collection.py')
    path = tmp_path/'acceptance.json'
    path.write_text(json.dumps({'selection':'usgs','stop':'2026-11-07','stations':[
        {'path':'a','status':'published','sha256':'1'},
        {'path':'b','status':'published','sha256':'2'},
        {'path':'c','status':'failed'}]}))
    accepted,stop = m.resume_successes(path,'usgs',[{'path':'a','sha256':'1'},{'path':'b','sha256':'changed'}])
    assert set(accepted) == {'a'} and str(stop) == '2026-11-07'
    with pytest.raises(ValueError,match='selection'):
        m.resume_successes(path,'cdec-fnf',[])


def test_transport_retry_is_bounded(monkeypatch,tmp_path):
    m = load_script('update_station_collection.py')
    attempts = []
    def fail(*args,**kwargs):
        attempts.append(args[1])
        raise requests.Timeout('timeout')
    monkeypatch.setattr(m,'acquire_window',fail)
    monkeypatch.setattr(m.time,'sleep',lambda _:None)
    with pytest.raises(requests.Timeout):
        m.acquire_retry(tmp_path,tmp_path,sample(),'2020-01-01','2020-01-03')
    assert [p.name for p in attempts] == ['raw-0','raw-1']


def test_usgs_controller_owns_all_retries(monkeypatch, tmp_path):
    m = load_script('update_station_collection.py')
    monkeypatch.setenv('USGS_API_KEY', 'test-key')
    old = sample()
    old.metadata['provider'] = 'usgs'
    calls = []
    def fail(root, raw, previous, **kwargs):
        calls.append((raw, kwargs['request_control']))
        raise requests.Timeout('transport failure')
    monkeypatch.setattr(m, 'acquire_window', fail)
    with pytest.raises(requests.Timeout):
        m.acquire_retry(tmp_path, tmp_path/'task', old, '2020-01-01', '2020-01-03')
    assert len(calls) == 1
    assert calls[0][0].name == 'raw-0'
    assert isinstance(calls[0][1], m.ControlledRequest)


@pytest.mark.parametrize('publication_failure',[False,True])
@pytest.mark.parametrize('prepared_path,worker_failure',[(False,False),(True,False),(True,True)])
def test_station_failure_continues_but_publication_failure_stops(
        monkeypatch,tmp_path,publication_failure,prepared_path,worker_failure):
    m = load_script('update_station_collection.py')
    entries = [{'path':p,'sha256':p,'resolution':'daily'} for p in ('a','b','c')]
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT',str(tmp_path))
    monkeypatch.setenv('SLURM_JOB_ID','123')
    monkeypatch.setenv('USGS_API_KEY', 'test-key')
    monkeypatch.setattr('sys.argv',['update_station_collection.py','--selection','usgs', '--prepare-workers', '0'])
    monkeypatch.setattr(m,'operation_lock',lambda _:nullcontext())
    monkeypatch.setattr(m,'recover_pending',lambda _:None)
    monkeypatch.setattr(m,'active_catalog',lambda _:tmp_path/'catalog.json')
    monkeypatch.setattr(m,'read_catalog',lambda _:{'entries':entries})
    monkeypatch.setattr(m,'selected',lambda *_:True)
    monkeypatch.setattr(m,'read_station',lambda p:p.name)
    def acquire(root,task,old,*args):
        if old == 'b' and not publication_failure and not worker_failure:
            raise ValueError('bad station response')
        return sample(),{}
    monkeypatch.setattr(m,'acquire_retry',acquire)
    monkeypatch.setattr(m,'retain_omitted',lambda *a,**k:(sample(),[]))
    published = []
    def publish(root,entry,*args):
        if entry['path'] == 'b' and publication_failure:
            raise ValueError('catalog conflict')
        published.append(entry['path'])
        return {'status':'published','entry':entry}
    monkeypatch.setattr(m,'publish_window',publish)
    if prepared_path:
        from concurrent.futures import Future
        from types import SimpleNamespace
        monkeypatch.setattr('sys.argv', ['update_station_collection.py', '--selection', 'usgs',
                                        '--prepare-workers', '4', '--limit', '3'])
        monkeypatch.setattr(m, 'write_station', lambda *a, **k: None)
        monkeypatch.setattr(m, 'PreparedPublisher', lambda _: SimpleNamespace(
            publish=lambda prepared: publish(tmp_path, prepared['before'])))
        def submit(*args):
            future = Future()
            if worker_failure and args[2]['path'] == 'b' and not publication_failure:
                future.set_exception(ValueError('preparation failed'))
            else:
                future.set_result({'before': args[2], 'timings': {}, 'omitted_keys_retained': []})
            return future
        monkeypatch.setattr(m, 'ProcessPoolExecutor', lambda **kw: nullcontext(SimpleNamespace(submit=submit)))
    if publication_failure:
        with pytest.raises(ValueError,match='catalog conflict'):
            m.main()
        assert published == ['a']
    else:
        with pytest.raises(SystemExit):
            m.main()
        assert published == ['a','c']
        report = json.loads((tmp_path/'observations/work/station-cycles/123/acceptance.json').read_text())
        assert report['status'] == 'partial' and report['failed_paths'] == ['b']
        if prepared_path:
            assert not (tmp_path/'observations/status/stations/usgs-latest.json').exists()
