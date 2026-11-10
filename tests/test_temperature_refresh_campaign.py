import json
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset
from test_nwm_forcing_coordinator import load_script

from hydro_ops.forcing.rain_repair_writer import digest
from hydro_ops.forcing.temperature_refresh import replace_hours


def test_shared_day_has_one_writer_and_excludes_pilot(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    planner = load_script('submit_temperature_refresh_campaign.py')
    publisher = load_script('publish_temperature_refresh_trial.py')
    streams = {'retro':{'windows':[{'day':d} for d in ('2026-04-11','2026-04-12','2026-04-13')]}}
    rows = planner.calendar_rows(streams,{('retro','2026-04-12')})
    assert len(rows) == 4
    assert rows[1]['day'] == '2026-04-11' and rows[1]['changed_hours'] == list(range(12))
    rows = planner.calendar_rows(streams,set())
    assert len(rows) == 4
    assert rows[1]['changed_hours'] == list(range(24))
    (tmp_path/'plan.json').write_text(json.dumps({'files':rows}))
    accepted = {'version':2,'status':'passed_candidate_only','files':rows,
                'changed_variables':['T2D','Q2D','LWDOWN'],'plan_sha256':digest(tmp_path/'plan.json')}
    scopes = publisher.publication_scopes(accepted,tmp_path)
    assert list(map(str,scopes['retro'][1])) == ['2026-04-10','2026-04-11','2026-04-12','2026-04-13']
    accepted['files'] = rows+rows[:1]
    with pytest.raises(ValueError,match='Duplicate'):
        publisher.publication_scopes(accepted,tmp_path)


def test_replace_two_windows_preserves_rain_and_unselected_hours(tmp_path):
    original,window,output = [tmp_path/n for n in ('original.nc','window.nc','output.nc')]
    for path in (original,window):
        with Dataset(path,'w') as ds:
            for dim,n in (('time',24),('y',2),('x',2)):
                ds.createDimension(dim,n)
            time = ds.createVariable('time','f8',('time',))
            time.units = 'hours since 2026-04-11 00:00:00'
            time[:] = np.arange(24)
            for name,value in {'T2D':280,'Q2D':0.004,'PSFC':100000,'LWDOWN':300,'RAINRATE':0.002}.items():
                v = ds.createVariable(name,'f4',('time','y','x'),zlib=True,chunksizes=(1,2,2),fill_value=-9999)
                v[:] = value
    with Dataset(window,'r+') as ds:
        ds['T2D'][:] = 282
    mapping = {0:(window,0),12:(window,12)}
    replace_hours(original,output,mapping,np.ones((2,2),bool),np.ones((2,2),bool),{})
    with Dataset(original) as a, Dataset(output) as b:
        np.testing.assert_array_equal(a['RAINRATE'][:],b['RAINRATE'][:])
        assert np.all(b['T2D'][0] == 282) and np.all(b['T2D'][12] == 282)
        np.testing.assert_array_equal(a['Q2D'][1:12],b['Q2D'][1:12])
        np.testing.assert_array_equal(a['LWDOWN'][13:],b['LWDOWN'][13:])
    with pytest.raises(ValueError,match='time mismatch'):
        replace_hours(original,tmp_path/'bad.nc',{0:(window,1)},np.ones((2,2),bool),np.ones((2,2),bool),{})
