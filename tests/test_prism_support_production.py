from argparse import Namespace
from datetime import date
from importlib import import_module

import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.forcing.prism_support_production import candidate_path


def test_candidate_guard(tmp_path):
    with pytest.raises(ValueError):
        candidate_path(tmp_path / "forcing/outputs/conus/retro/file", tmp_path)
    assert candidate_path(tmp_path / "forcing/work/test/file", tmp_path)


@pytest.mark.parametrize('direct', [False, True])
@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('adapter_name', ['prism_support_production', 'nrt_support_production'])
def test_support_adapter_roundtrip(tmp_path, monkeypatch, direct, compact, adapter_name):
    adapter = import_module(f'hydro_ops.forcing.{adapter_name}')
    monkeypatch.setenv("HYDRO_OPS_PROJECT_ROOT", str(tmp_path))
    work = tmp_path / "forcing/work/test"
    work.mkdir(parents=True)
    baseline = work / "base.nc"
    with Dataset(baseline, "w") as ds:
        for name, size in [("time", 24), ("y", 2), ("x", 2)]:
            ds.createDimension(name, size)
        t = ds.createVariable("time", "i8", ("time",))
        t.units = "hours since 2014-01-14 12:00:00"
        t[:] = np.arange(24)
        ds.createVariable("RAINRATE", "f4", ("time", "y", "x"), fill_value=-9999.0)[:] = 0
        ds.createVariable("T2D", "f4", ("time", "y", "x"), fill_value=-9999.0)[:] = 280
        ds.createVariable("lat", "f8", ("y", "x"))[:] = [[35, 35], [36, 36]]
        ds.createVariable("lon", "f8", ("y", "x"))[:] = [[-120, -119], [-120, -119]]
    mask = work / "mask.nc"
    with Dataset(mask, "w") as ds:
        ds.createDimension("y", 2)
        ds.createDimension("x", 2)
        ds.createVariable("XLAND", "i4", ("y", "x"))[:] = [[1, 1], [1, 2]]
    prism = work / "prism.nc"
    with Dataset(prism, "w") as ds:
        ds.createDimension("y", 2)
        ds.createDimension("x", 2)
        ds.createVariable("ppt", "f4", ("y", "x"))[:] = [[10, 20], [0, 3]]
    weights = work / "weights.nc"
    with Dataset(weights, "w") as ds:
        ds.createDimension("links", 4)
        ds.createDimension("rank", 2)
        ds.createDimension("weight", 1)
        ds.createVariable("src_grid_dims", "i4", ("rank",))[:] = [2, 2]
        ds.createVariable("dst_grid_dims", "i4", ("rank",))[:] = [2, 2]
        for name in ("src_address", "dst_address"):
            ds.createVariable(name, "i4", ("links",))[:] = [1, 2, 3, 4]
        ds.createVariable("remap_matrix", "f8", ("links", "weight"))[:] = 1
        ds.createVariable("dst_grid_frac", "f8", ("links",))[:] = [1, 1, 1, 0.01]
        ds.createVariable("dst_grid_area", "f8", ("links",))[:] = 1
    native = work / "native/2014"
    native.mkdir(parents=True)
    for day in (14, 15):
        with Dataset(native / f"NLDAS_FORA0125_H.A201401{day}.020.nc", "w") as ds:
            for name, size in [("time", 24), ("lat", 2), ("lon", 2)]:
                ds.createDimension(name, size)
            t = ds.createVariable("time", "i8", ("time",))
            t.units = f"hours since 2014-01-{day} 00:00:00"
            t[:] = np.arange(24)
            ds.createVariable("lat", "f8", ("lat",))[:] = [35, 36]
            ds.createVariable("lon", "f8", ("lon",))[:] = [-120, -119]
            v = ds.createVariable("Rainf", "f4", ("time", "lat", "lon"))
            v.units = "kg m-2"
            v[:] = 1
    args = Namespace(
        daily_output=work / "out.nc",
        day=date(2014, 1, 15),
        active_mask=mask,
        nldas_root=native.parent,
        diagnostics=work / "diagnostics.nc",
        hour_indices=list(range(24)),
        hours=[baseline] * 24,
        prism=prism,
        prism_variable="ppt",
        weights=weights,
        max_iterations=20,
        temperature_corrected_day=None,
        work_directory=work,
        revision="stable",
        direct_fallback=direct,
        compact_rain_window=compact,
    )
    if direct:
        monkeypatch.setattr(f'hydro_ops.forcing.{adapter_name}.fit_daily_depth',
                            lambda *a, **kw: (np.array([0., 0., 0., np.nan]),
                                              {'iterations': kw['max_iterations']}))
    assert adapter.run(args) == 0
    with Dataset(args.daily_output) as ds:
        if adapter_name == 'nrt_support_production':
            assert ds['prism_timing_source_id'].chunking() == ds['RAINRATE'].chunking()
        assert ds.prism_reconciliation_accepted == "true"
        assert ds.prism_acceptance_class == ('accepted_fallback' if direct else 'accepted_strict')
        assert ds.prism_reconciliation_converged == ('false' if direct else 'true')
        if compact:
            assert set(ds.variables) == {'time', 'RAINRATE', 'prism_timing_source_id'}
        else:
            np.testing.assert_array_equal(ds["T2D"][:], np.full((24, 2, 2), 280))
        np.testing.assert_allclose(
            ds["RAINRATE"][:].sum(axis=0) * 3600, [[10, 20], [0, 0]], rtol=1e-6
        )
