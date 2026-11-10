#!/usr/bin/env python3
"""Read-only geometry and pairwise feasibility audit of rejected PRISM cells."""

import argparse
import json
import os
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from scipy.optimize import linprog

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
except ModuleNotFoundError:
    plt = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--conus-report', type=Path)
    parser.add_argument('--focus', type=int)
    args = parser.parse_args()
    if args.conus_report and args.focus is None:
        parser.error('--conus-report requires --focus')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    output = root / 'forcing/work/prism-spatial-conflicts' / os.environ['SLURM_JOB_ID']
    output.mkdir(parents=True, exist_ok=False)
    weights = root / 'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
    operator = ConservativeOperator.from_cdo(weights)
    mask_path = (root / 'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
                 if args.conus_report else root / 'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc')
    with Dataset(mask_path) as ds:
        mask = np.asarray(ds['active' if args.conus_report else 'model_mask'][:]).reshape(-1)
        forcing = np.asarray(ds['keep' if args.conus_report else 'forcing_mask'][:]).reshape(-1)
        lat = np.asarray(ds['lat'][:]).reshape(-1)
        lon = np.asarray(ds['lon'][:]).reshape(-1)
        shape = ds['lat'].shape
        window = ({'south_north_start': 0, 'west_east_start': 0} if args.conus_report
                  else json.loads(ds.source_window))
    reports = []
    cases = ([(json.loads(args.conus_report.read_text())['day'], args.focus)] if args.conus_report
             else [('2002-01-17', 345764), ('2016-04-15', 470737)])
    for day, focus in cases:
        report_path = args.conus_report or root / f'forcing/work/prism-fallback-tests/5081235/{day}.json'
        report = json.loads(report_path.read_text())
        neighborhood = report['local_fallback']['neighbor_targets']
        targets = {v['index']: v['target_mm'] for v in neighborhood}
        sources = np.unique(operator.source_index[operator.target_index == focus])
        neighbors = np.unique(operator.target_index[np.isin(operator.source_index, sources)])
        neighbors = np.array([i for i in neighbors if i in targets])
        selected = np.isin(operator.target_index, neighbors)
        all_sources = np.unique(operator.source_index[selected])
        rows = []
        with Dataset(weights) as ds:
            width = int(ds['src_grid_dims'][0])
            coordinates = {}
            for index in neighbors:
                coordinates[int(index)] = [float(np.rad2deg(ds['dst_grid_center_lon'][index]) % 360),
                                            float(np.rad2deg(ds['dst_grid_center_lat'][index]))]
                coordinates[int(index)][0] = (coordinates[int(index)][0]+180) % 360-180
                links = operator.target_index == index
                src, weight = operator.source_index[links], operator.weight[links]
                local = (src // width-window['south_north_start'])*shape[1] + src % width-window['west_east_start']
                normalized = weight / weight.sum()
                rows.append({'index': int(index), 'lon_lat': coordinates[int(index)],
                             'target_mm': targets[index], 'source_count': len(src),
                             'active_model_sources': int(mask[local].sum()),
                             'retained_forcing_sources': int(forcing[local].sum()),
                             'dst_grid_frac': float(ds['dst_grid_frac'][index]),
                             'weight_sum': float(weight.sum()),
                             'links': [{'source': int(s), 'weight': float(w)}
                                       for s, w in zip(src, normalized, strict=True)]})
        rowmap = {r['index']: r for r in rows}
        pairs = []
        for neighbor in neighbors:
            if neighbor == focus:
                continue
            a, b = rowmap[focus], rowmap[int(neighbor)]
            cols = sorted({v['source'] for r in (a, b) for v in r['links']})
            matrix = np.array([[{v['source']: v['weight'] for v in r['links']}.get(c, 0)
                                for c in cols] for r in (a, b)])
            target = np.array([a['target_mm'], b['target_mm']])
            tolerance = .01+.001*target
            result = linprog(np.r_[np.zeros(len(cols)), 1],
                             A_ub=np.vstack((np.column_stack((matrix, -tolerance)),
                                              np.column_stack((-matrix, -tolerance)))),
                             b_ub=np.r_[target, -target], bounds=(0, None), method='highs')
            pairs.append({'indices': [focus, int(neighbor)], 'solver_success': bool(result.success),
                          'minimum_tolerance_multiplier': float(result.x[-1]) if result.success else None,
                          'optimal_estimates_mm': (matrix @ result.x[:-1]).tolist() if result.success else None,
                          'targets_mm': target.tolist()})
        strongest = max((p for p in pairs if p['solver_success']), key=lambda p: p['minimum_tolerance_multiplier'])
        if plt is None:
            reports.append({'day': day, 'focus': focus, 'rows': rows, 'pairs': pairs,
                            'strongest_pair': strongest, 'map_status': 'matplotlib unavailable'})
            print(day, json.dumps(strongest), flush=True)
            continue
        x0, y0 = coordinates[focus]
        local = (all_sources // width-window['south_north_start'])*shape[1]+all_sources % width-window['west_east_start']
        fig, ax = plt.subplots(figsize=(9, 8), constrained_layout=True)
        for r in rows:
            x, y = r['lon_lat']
            ax.add_patch(Rectangle((x-1/48, y-1/48), 1/24, 1/24, fill=False, edgecolor='gray'))
            ax.text(x, y+0.014, f"{r['index']}\n{r['target_mm']:.3f} mm", fontsize=8, ha='center')
        ax.scatter(lon[local], lat[local], c=np.where(mask[local], 'tab:green', 'tab:orange'), s=22,
                   marker='s', label='Contributing NWM centers: green active / orange inactive')
        ax.scatter([x0], [y0], marker='x', color='red', s=90, label='Focus PRISM center')
        ax.set(xlabel='Longitude', ylabel='Latitude', title=f'{day}: conservative support and PRISM totals')
        ax.set_aspect(1/np.cos(np.deg2rad(y0)))
        ax.legend(loc='lower left', fontsize=8)
        fig.savefig(output / f'{day}.png', dpi=150)
        plt.close(fig)
        reports.append({'day': day, 'focus': focus, 'rows': rows, 'pairs': pairs,
                        'strongest_pair': strongest})
        print(day, json.dumps(strongest), flush=True)
    (output / 'geometry.json').write_text(json.dumps(reports, indent=2)+'\n')


if __name__ == '__main__':
    main()
