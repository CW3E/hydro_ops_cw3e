from datetime import date

import numpy as np
import pytest
from netCDF4 import Dataset
from test_nwm_forcing_coordinator import load_script


def test_independent_window_reads_calendar_halves(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('diagnose_temperature_refresh.py')
    paths = [tmp_path/'a.nc',tmp_path/'b.nc']
    for j,path in enumerate(paths):
        with Dataset(path,'w') as ds:
            ds.createDimension('time',24)
            ds.createDimension('y',2)
            ds.createDimension('x',2)
            t = ds.createVariable('time','f8',('time',))
            t.units = 'hours since 2026-05-02 00:00:00'
            t[:] = np.arange(24)+24*j
            v = ds.createVariable('T2D','f4',('time','y','x'))
            v[:] = (np.arange(24)+24*j)[:,None,None]+np.zeros((24,2,2))
    sequence = module.records(paths,date(2026,5,3))
    assert sequence[0] == (paths[0],12) and sequence[-1] == (paths[1],11)
    low,high = module.extrema(sequence)
    assert np.all(low==12) and np.all(high==35)
    with pytest.raises(ValueError,match='Duplicate'):
        module.records(paths+paths,date(2026,5,3))
    with pytest.raises(ValueError,match='Missing'):
        module.records(paths[:1],date(2026,5,3))


def test_missing_required_values_cannot_hide_error(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('diagnose_temperature_refresh.py')
    mask = np.array([[True,False]])
    assert module.maximum_error(np.array([[3,np.nan]]),np.array([[1,np.nan]]),mask) == 2
    for value in (np.nan,np.inf,-np.inf):
        for a,b in (([[value,0]],[[1,0]]),([[1,0]],[[value,0]])):
            with pytest.raises(ValueError,match='Nonfinite'):
                module.maximum_error(a,b,mask)
    with pytest.raises(ValueError,match='empty'):
        module.maximum_error([[1,2]],[[1,2]],np.array([[False,False]]))
    with pytest.raises(ValueError,match='Nonfinite'):
        module.maximum_error(np.ma.array([[1,2]],mask=[[True,False]]),[[1,2]],mask)


def test_operational_mask_not_broader_keep(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('diagnose_temperature_refresh.py')
    path = tmp_path/'mask.nc'
    with Dataset(path,'w') as ds:
        ds.createDimension('x',2)
        ds.createVariable('active','i1',('x',))[:] = [1,0]
        ds.createVariable('keep','i1',('x',))[:] = [1,1]
    np.testing.assert_array_equal(module.operational_active(path),[True,False])
    with Dataset(path,'r+') as ds:
        ds['keep'][:] = [0,1]
    with pytest.raises(ValueError,match='Invalid operational'):
        module.operational_active(path)
