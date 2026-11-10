from pathlib import Path

import numpy as np
from test_nwm_forcing_coordinator import load_script


def test_curve_signatures(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    m=load_script('diagnose_original_temperature_curve.py')
    x=np.arange(24,dtype=float)[:,None]+np.zeros((24,3))+270
    y=2*x+3
    y[10,1]+=1
    y[0,2]=np.nan
    f=m.fit_curve(x,y)
    assert f['complete'].tolist()==[True,True,False]
    assert abs(f['slope'][0]-2)<1e-10 and f['residual'][0]<1e-10
    assert f['residual'][1]>.5
    assert m.fit_curve(x,x)['maximum_difference'].max()==0
