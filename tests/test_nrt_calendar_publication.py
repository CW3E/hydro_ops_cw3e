import json
from datetime import date

import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.forcing.native_donor import FIELDS
from hydro_ops.forcing.nrt_calendar_publication import publish
from hydro_ops.forcing.nrt_cycle import day_path, identity


def setup(root, hole=False):
    (root/'config').mkdir()
    (root/'config/nrt_gfs.toml').write_text('envelope = "mask.nc"\n')
    with Dataset(root/'mask.nc', 'w') as ds:
        ds.createDimension('y', 2)
        ds.createDimension('x', 2)
        ds.createVariable('active', 'i1', ('y', 'x'))[:] = [[1, 0], [0, 0]]
        ds.createVariable('keep', 'i1', ('y', 'x'))[:] = [[1, 1], [0, 0]]
    day = date(2026, 10, 29)
    base = day_path(root/'baseline', day)
    base.parent.mkdir(parents=True)
    base.write_bytes(b'baseline')
    base.with_name(base.name+'.nrt-receipt.json').write_text(json.dumps({
        'status': 'passed', 'published_identity': identity(base), 'gfs_hours': 0,
        'hourly_primary_sources': ['nldas2']*24}))
    target = day_path(root/'output', day)
    target.parent.mkdir(parents=True)
    with Dataset(target, 'w') as ds:
        for name, size in [('time', 24), ('y', 2), ('x', 2)]:
            ds.createDimension(name, size)
        t = ds.createVariable('time', 'f8', ('time',))
        t.units = 'hours since 2026-10-29 00:00:00'
        t[:] = np.arange(24)
        ds.cnrfc_stage4_policy = 'test'
        ds.prism_reconciliation_accepted = 'true'
        for name in FIELDS:
            var = ds.createVariable(name, 'f4', ('time', 'y', 'x'), fill_value=-9999.)
            var[:] = 1
            if hole:
                var[0, 0, 0] = -9999.
    target.with_name(target.name+'.manifest.json').write_text(json.dumps({'verified': True}))
    target.with_name(target.name+'.nrt-receipt.json').write_text(json.dumps({'status': 'passed', 'sha256': 'obsolete'}))
    return target, day


def test_reaudits_masks_and_replaces_receipt(tmp_path):
    target, day = setup(tmp_path)
    original = target.read_bytes()
    receipt = publish(tmp_path, target, target, tmp_path/'baseline', day, tmp_path/'scratch', tmp_path/'state')
    assert receipt['prism_constrained'] and receipt['complete_day']
    assert receipt['published_identity'] == identity(target)
    assert receipt['sha256'] != 'obsolete'
    assert receipt['gfs_hours'] == 0
    assert receipt['audit_record_writes'] == 24*len(FIELDS)
    with Dataset(target) as ds:
        assert np.ma.getmaskarray(ds['RAINRATE'][:])[:, 1, :].all()
        assert (ds['RAINRATE'][:, 0, 0] == 1).all()
    assert (tmp_path/'state/2026-10-29/original.LDASIN_DOMAIN1').read_bytes() == original


def test_rejects_active_holes_without_replacing_original(tmp_path):
    target, day = setup(tmp_path, hole=True)
    original = target.read_bytes()
    with pytest.raises(ValueError, match='active holes'):
        publish(tmp_path, target, target, tmp_path/'baseline', day, tmp_path/'scratch', tmp_path/'state')
    assert target.read_bytes() == original
    assert json.loads(target.with_name(target.name+'.nrt-receipt.json').read_text())['sha256'] == 'obsolete'
