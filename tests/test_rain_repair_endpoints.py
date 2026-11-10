import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


def module(monkeypatch, name):
    directory = Path(__file__).resolve().parents[1]/'bin'
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(name, directory/f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def test_summary_discovery_includes_year_only_monthly(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'finalize_nrt_rain_summaries')
    names = ['daily/2026/04/20260401.LDASIN_DOMAIN1.daily',
             'monthly/2026/202604.LDASIN_DOMAIN1.monthly']
    for name in names:
        path = tmp_path/'nrt'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        path.with_name(path.name+'.subset.json').touch()
    assert {str(p.relative_to(tmp_path/'nrt')) for p in
            mod.discover(tmp_path, ('nrt',), ('daily', 'monthly'))} == set(names)


def test_boundary_comparison_ignores_only_outside_keep(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'finish_rain_repair_boundaries')
    paths = [tmp_path/'a.nc', tmp_path/'b.nc']
    for path in paths:
        with Dataset(path, 'w') as ds:
            ds.createDimension('time', 24)
            ds.createDimension('y', 1)
            ds.createDimension('x', 2)
            for name in ('RAINRATE', 'prism_timing_source_id'):
                ds.createVariable(name, 'f4', ('time', 'y', 'x'))[:] = 1
    with Dataset(paths[1], 'r+') as ds:
        ds['RAINRATE'][:, :, 1] = 5
    keep = np.array([[True, False]])
    mod.same_window(*paths, keep)
    with Dataset(paths[1], 'r+') as ds:
        ds['RAINRATE'][0, 0, 0] = 2
    with pytest.raises(AssertionError):
        mod.same_window(*paths, keep)


def test_audit_bounds_and_stale_summary(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'audit_repaired_forcing_lineage')
    selected = mod.selected(2026, 'retro')
    assert ('hourly', date(2026, 4, 18)) in selected
    assert ('daily', date(2026, 4, 17)) in selected
    assert ('daily', date(2026, 4, 18)) not in selected
    assert ('monthly', date(2026, 4, 1)) not in selected
    assert ('monthly', date(2026, 3, 1)) not in mod.selected(2026, 'nrt')
    path = tmp_path/'source'
    path.write_text('original')
    row = {'path': str(path), 'bytes': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns}
    mod.source_current([row])
    path.write_text('replacement')
    with pytest.raises(ValueError, match='Stale summary source'):
        mod.source_current([row])


@pytest.mark.parametrize(('resolution', 'value'), [('daily', 'P1D'), ('monthly', 'P1M')])
def test_summary_audit_iso_resolution_and_strict_provenance(tmp_path, monkeypatch, resolution, value):
    mod = module(monkeypatch, 'audit_repaired_forcing_lineage')
    source = tmp_path/'source'
    source.write_text('original')
    summary = tmp_path/'summary.nc'
    row = {'path': str(source), 'bytes': source.stat().st_size,
           'mtime_ns': source.stat().st_mtime_ns}
    with Dataset(summary, 'w') as ds:
        ds.source_files = json.dumps([row])
        ds.temporal_resolution = value
    mod.audit_summary(summary, resolution)
    for wrong in ('daily', 'monthly', 'P1M' if value == 'P1D' else 'P1D'):
        with Dataset(summary, 'r+') as ds:
            ds.temporal_resolution = wrong
        with pytest.raises(ValueError, match='Wrong summary temporal resolution'):
            mod.audit_summary(summary, resolution)
    with Dataset(summary, 'r+') as ds:
        ds.temporal_resolution = value
    source.write_text('replacement')
    with pytest.raises(ValueError, match='Stale summary source'):
        mod.audit_summary(summary, resolution)
    source.unlink()
    with pytest.raises(FileNotFoundError):
        mod.audit_summary(summary, resolution)
