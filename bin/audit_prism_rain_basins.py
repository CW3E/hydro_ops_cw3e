#!/usr/bin/env python3
"""Diagnose PRISM-window and monthly basin totals from accepted wet-month repairs.

Uses the audited NWM-to-PRISM stored-value residuals and fractional basin
coverage on PRISM cells. It is a grid-scale basin diagnostic, not an exact
polygon integration of sub-PRISM NWM cells at basin boundaries.
"""
import argparse
import hashlib
import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from netCDF4 import Dataset
from pyproj import Transformer

from hydro_ops.forcing.prism_support_production import candidate_path
from hydro_ops.forcing.prism_volume_audit import region_totals


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', type=Path, required=True)
    parser.add_argument('--basins', type=Path, required=True)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--days', type=int, required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    trial = json.loads((args.trial / 'acceptance.json').read_text())
    if trial['status'] != 'passed':
        raise ValueError('Pipeline must pass before scientific diagnostics')
    out = candidate_path(root / 'forcing/work/prism-basin-audit' / os.environ['SLURM_JOB_ID'], root)
    out.mkdir(parents=True, exist_ok=False)
    copied = out / 'forecast_basins.gpkg'
    shutil.copy2(args.basins, copied)
    basins = gpd.read_file(copied).to_crs(4326).dissolve(by='Basin').reset_index()
    boundary = root / 'nwm/static/domains/cnrfc/boundaries/cnrfc_boundaries.gpkg'
    region = gpd.read_file(boundary, layer='cnrfc_hu18_union').to_crs(4326)
    geometries = [('cnrfc_reporting_region', region.geometry.union_all())]
    geometries += [(str(row.Basin), row.geometry) for row in basins.itertuples()]
    weight_file = root / 'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
    with Dataset(weight_file) as ds:
        lon = np.rad2deg(np.asarray(ds['dst_grid_corner_lon'][:]))
        lat = np.rad2deg(np.asarray(ds['dst_grid_corner_lat'][:]))
        cell_area = np.asarray(ds['dst_grid_area'][:])
    lon = (lon + 180) % 360 - 180
    minx, maxx, miny, maxy = lon.min(axis=1), lon.max(axis=1), lat.min(axis=1), lat.max(axis=1)
    projection = Transformer.from_crs(4326, 5070, always_xy=True)
    from shapely.ops import transform

    fractions = {}
    for name, geometry in geometries:
        geometry = shapely.make_valid(geometry)
        x0, y0, x1, y1 = geometry.bounds
        indices = np.flatnonzero((maxx >= x0) & (minx <= x1) & (maxy >= y0) & (miny <= y1))
        x, y = projection.transform(lon[indices], lat[indices])
        pixels = shapely.polygons(np.stack((x, y), axis=-1))
        polygon = transform(projection.transform, geometry)
        fraction = shapely.area(shapely.intersection(pixels, polygon)) / shapely.area(pixels)
        fractions[name] = (indices, np.clip(fraction, 0, 1))
    target_days, estimate_days, covered_days = [], [], []
    labels, classes = [], []
    for i in range(args.days):
        day = args.start + timedelta(days=i)
        stem = args.trial / 'windows' / day.strftime('%Y/%m/%Y%m%d.prism_precipitation_diagnostics.nc')
        window_report = json.loads(stem.with_suffix('.json').read_text())
        if window_report['status'] != 'published_candidate':
            raise ValueError(f'Unaccepted window {day}')
        with Dataset(stem.with_suffix('.nc')) as ds:
            target = np.ma.asarray(ds['hard_target_mm'][:]).filled(np.nan)
            residual = np.ma.asarray(ds['residual_mm'][:]).filled(np.nan)
            covered = np.asarray(ds['covered_area'][:])
        target_days.append(target)
        estimate_days.append(target + residual)
        covered_days.append(covered)
        labels.append(str(day))
        classes.append(window_report['acceptance_class'])
    targets, estimates, covered = map(np.stack, (target_days, estimate_days, covered_days))
    # A fixed common mask avoids changing basin area from one day to another.
    common = np.all(np.isfinite(targets) & np.isfinite(estimates) & (covered > 0), axis=0)
    if not np.allclose(covered, covered[0], rtol=0, atol=0):
        raise ValueError('Coverage changed across observation windows')
    fractions['all_common_prism_support'] = (np.flatnonzero(common), np.ones(common.sum()))
    reports = {}
    for name, (indices, fraction) in fractions.items():
        full_area = float(np.sum(cell_area[indices] * fraction))
        weights = np.where(common[indices], covered[0, indices] * fraction, 0)
        if weights.sum() == 0:
            reports[name] = {'status': 'no_common_support'}
            continue
        daily = [dict(day=label, acceptance_class=kind,
                      **region_totals(t[indices], e[indices], weights))
                 for label, kind, t, e in zip(labels, classes, targets, estimates, strict=True)]
        monthly = region_totals(targets[:, indices].sum(axis=0),
                                estimates[:, indices].sum(axis=0), weights)
        wet = [d for d in daily if d['prism_mean_mm'] >= 1]
        reports[name] = {
            'status': 'diagnostic_only', 'daily': daily, 'month_sum': monthly,
            'prism_cells_with_support': int(np.count_nonzero(weights)),
            'covered_fraction_of_in_grid_basin_area': float(weights.sum() / full_area),
            'max_daily_absolute_bias_mm': max(abs(d['bias_mm']) for d in daily),
            'max_wet_day_absolute_bias_percent': max((abs(d['bias_percent']) for d in wet), default=None)}
    ordered = sorted(((k, v) for k, v in reports.items() if 'month_sum' in v),
                     key=lambda item: abs(item[1]['month_sum']['bias_mm']), reverse=True)
    report = {
        'status': 'diagnostics_complete', 'production_modified': False,
        'scientific_acceptance': 'requires_review', 'trial': str(args.trial),
        'forecast_basins_source': str(args.basins), 'forecast_basins_sha256': digest(copied),
        'reporting_boundary_sha256': digest(boundary),
        'start_label': labels[0], 'end_label': labels[-1],
        'time_definition': 'Sum of labeled daily PRISM 12-12 UTC windows; NOT UTC calendar month or separate PRISM monthly product',
        'spatial_definition': 'Audited stored NWM totals conservatively mapped to PRISM; fractional basin overlap on PRISM cells, common eligible support only',
        'boundary_caveat': 'Coverage within partially supported PRISM cells is assumed uniform; small basin/edge results need interpretation. Areas outside PRISM are excluded.',
        'common_supported_cells': int(common.sum()),
        'eligible_cells_per_day': np.sum(np.isfinite(targets), axis=1).tolist(),
        'worst_monthly_bias_mm_regions': [k for k, _ in ordered[:20]], 'regions': reports}
    (out / 'basin_totals.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'report': str(out / 'basin_totals.json'),
                      'cnrfc_month': reports['cnrfc_reporting_region']['month_sum'],
                      'worst_regions': report['worst_monthly_bias_mm_regions']}, indent=2))


if __name__ == '__main__':
    main()
