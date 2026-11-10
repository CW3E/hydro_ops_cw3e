"""Small geometry integration fixture; run with the site's geospatial Python."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


def test_basin_audit_fixture(tmp_path):
    gpd = pytest.importorskip('geopandas')
    from shapely.geometry import box
    root = tmp_path
    geometry = box(-121, 35, -119, 36)
    basins = root / 'basins.gpkg'
    gpd.GeoDataFrame({'Basin': ['TEST']}, geometry=[geometry], crs=4326).to_file(basins)
    boundary = root / 'nwm/static/domains/cnrfc/boundaries/cnrfc_boundaries.gpkg'
    boundary.parent.mkdir(parents=True)
    gpd.GeoDataFrame(geometry=[geometry], crs=4326).to_file(boundary, layer='cnrfc_hu18_union')
    weights = root / 'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
    weights.parent.mkdir(parents=True)
    with Dataset(weights, 'w') as ds:
        ds.createDimension('cell', 2)
        ds.createDimension('corner', 4)
        ds.createVariable('dst_grid_corner_lon', 'f8', ('cell', 'corner'))[:] = np.deg2rad(
            [[-121, -120, -120, -121], [-120, -119, -119, -120]])
        ds.createVariable('dst_grid_corner_lat', 'f8', ('cell', 'corner'))[:] = np.deg2rad(
            [[35, 35, 36, 36], [35, 35, 36, 36]])
        ds.createVariable('dst_grid_area', 'f8', ('cell',))[:] = 1
    trial = root / 'forcing/work/trial'
    windows = trial / 'windows/2023/01'
    windows.mkdir(parents=True)
    (trial / 'acceptance.json').write_text(json.dumps({'status': 'passed'}))
    for label in ('20230101', '20230102'):
        path = windows / f'{label}.prism_precipitation_diagnostics.nc'
        with Dataset(path, 'w') as ds:
            ds.createDimension('cell', 2)
            for name, data in [('hard_target_mm', [1, 3]), ('residual_mm', [.1, -.1]),
                               ('covered_area', [1, 1])]:
                ds.createVariable(name, 'f8', ('cell',))[:] = data
        path.with_suffix('.json').write_text(json.dumps(
            {'status': 'published_candidate', 'acceptance_class': 'accepted_fallback'}))
    script = Path(__file__).resolve().parents[1] / 'bin/audit_prism_rain_basins.py'
    env = dict(os.environ, HYDRO_OPS_PROJECT_ROOT=str(root), SLURM_JOB_ID='test')
    subprocess.run([sys.executable, str(script), '--trial', str(trial), '--basins', str(basins),
                    '--start', '2023-01-01', '--days', '2'], env=env, check=True)
    report = json.loads((root / 'forcing/work/prism-basin-audit/test/basin_totals.json').read_text())
    assert report['status'] == 'diagnostics_complete'
    assert report['scientific_acceptance'] == 'requires_review'
    total = report['regions']['all_common_prism_support']['month_sum']
    assert total['prism_mean_mm'] == 4
    assert total['bias_mm'] == pytest.approx(0, abs=1e-10)
