import h5py
import numpy as np
import pytest

from hydro_ops.forcing.physics import relative_humidity_from_specific_humidity
from hydro_ops.forcing.temperature_refresh import coupled, unchanged_payloads


def test_coupling_preserves_rh_and_identity():
    t = np.array([280., 290.])
    q = np.array([.003, .008])
    p = np.array([90000., 100000.])
    lw = np.array([250., 300.])
    identity = coupled(t,q,p,lw,t)
    np.testing.assert_allclose(identity['Q2D'],q)
    np.testing.assert_allclose(identity['LWDOWN'],lw)
    changed = coupled(t,q,p,lw,t+2)
    np.testing.assert_allclose(relative_humidity_from_specific_humidity(changed['Q2D'],t+2,p,phase='water'),
                               relative_humidity_from_specific_humidity(q,t,p,phase='water'))


def test_payload_audit_protects_rain_and_qc(tmp_path):
    import shutil
    a,b = tmp_path/'a.nc',tmp_path/'b.nc'
    with h5py.File(a,'w') as ds:
        for name in ['RAINRATE','T2D','prism_timing_source_id']:
            ds.create_dataset(name,data=np.ones((2,2)),chunks=(1,2),compression='gzip')
    shutil.copy2(a,b)
    with h5py.File(b,'r+') as ds:
        ds['T2D'][0]=2
    assert unchanged_payloads(a,b)==4
    with h5py.File(b,'r+') as ds:
        ds['RAINRATE'][0]=2
    with pytest.raises(ValueError,match='Payload changed'):
        unchanged_payloads(a,b)
