import numpy as np
import pytest
from test_nwm_forcing_coordinator import load_script


def test_static_vs_varying_pattern():
    m=load_script('compare_temperature_target_midpoints.py')
    x=np.array([[1.,2.,3.],[1.,2.,3.],[1.,2.,3.]])
    r=m.compare(x,['a','b','c'])
    assert r['fixed_spatial_fraction_of_squared_error']==1
    assert r['cells_spread_le_0_001k']==3
    assert r['varying_component_rms_k']==0
    x[1]*=-1
    r=m.compare(x,['a','b','c'])
    assert r['fixed_spatial_fraction_of_squared_error']==pytest.approx(1/9)
    assert r['pairs']['a__b']['correlation']==pytest.approx(-1)
    assert r['cells_spread_gt_0_1k']==3
    x[0,0]=np.nan
    with pytest.raises(ValueError,match='Finite'):
        m.compare(x,['a','b','c'])


def test_undefined_correlation_and_no_error():
    m=load_script('compare_temperature_target_midpoints.py')
    assert m.correlation([1,1],[2,3]) is None
    assert m.compare(np.zeros((2,3)),['a','b'])['fixed_spatial_fraction_of_squared_error'] is None
