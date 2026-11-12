"""Plan, render month shards, and finalize an immutable multi-stream web test collection."""
import argparse
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import netCDF4
import numpy as np
from benchmark_nwm_history_stream import derive
from publish_web_raster_pilot import identity, write

from hydro_ops.forcing.web_rasters import corners, legends, mercator, render, save_indexed_png
from hydro_ops.wrf_hydro.climatology import PERCENTILES, percentile_rank

ROOT = Path(__file__).resolve().parents[1]
TABLES = {'cnrfc': (5260669, 5263647), 'ucrb': (5260677, 5263648)}


def next_month(day):
    return (day.replace(day=28)+timedelta(days=4)).replace(day=1)


def choose_timeline(assets):
    groups = {}
    for a in assets:
        key = (a['product'], a['variable'], a['resolution'], a['date'])
        groups.setdefault(key, []).append(a)
    return [{'product': k[0], 'variable': k[1], 'resolution': k[2], 'date': k[3],
             'preferredAsset': min(v, key=lambda a: {'retro': 0, 'nrt': 1}[a['stream']])['id'],
             'availableAssets': [a['id'] for a in v]} for k, v in sorted(groups.items())]


def plan(output, start, end, smoke=False):
    output.mkdir(parents=True, exist_ok=False)
    tasks, pins = [], {}
    for domain, (tile_job, month_job) in TABLES.items():
        tile_path = ROOT/f'nwm/status/{domain}/climatology/tiles-{tile_job}/acceptance.json'
        accepted = json.loads(tile_path.read_text())
        if accepted['status'] != 'passed' or accepted['source_model_campaign'] != 'repaired_rain_retro_v1':
            raise ValueError('Corrected accepted climatology required')
        paths = [tile_path, ROOT/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc']
        paths += [Path(t['path']) for t in accepted['tiles']]
        month_dir = ROOT/f'forcing/analysis/{domain}/retro/monthly-climatology/wy1980-2025/job_{month_job}'
        ma = json.loads((month_dir/'report.json').read_text())
        if ma['status'] != 'passed' or identity(ROOT/ma['output']) != ma['output_identity']:
            raise ValueError('Monthly climatology changed')
        paths += [month_dir/'report.json', ROOT/ma['output']]
        grid = ROOT/f'forcing/outputs/{domain}/retro/daily/2023/04/20230401.LDASIN_DOMAIN1.daily'
        paths.append(grid)
        for p in paths:
            pins[str(p.relative_to(ROOT))] = identity(p)
        current = start.replace(day=1)
        while current <= end:
            items = []
            for product in ('forcing', 'nwm'):
                for stream in ('retro', 'nrt'):
                    for resolution in (('daily', 'monthly') if product == 'forcing' else ('daily',)):
                        directory = ROOT/f'{product}/outputs/{domain}/{stream}/{resolution}'
                        suffix = 'LDASIN_DOMAIN1' if product == 'forcing' else 'LDASOUT_DOMAIN1'
                        candidates = (directory/f'{current:%Y/%m}').glob(f'*.{suffix}.daily') if resolution == 'daily' else (directory/f'{current:%Y}').glob(f'{current:%Y%m}.{suffix}.monthly')
                        for p in sorted(candidates):
                            key = p.name.split('.')[0]
                            day = date(int(key[:4]), int(key[4:6]), int(key[6:8]) if len(key) == 8 else 1)
                            if day < start or day > end or (resolution == 'monthly' and next_month(day) > end+timedelta(days=1)):
                                continue
                            pins[str(p.relative_to(ROOT))] = identity(p)
                            items.append({'source': str(p.relative_to(ROOT)), 'product': product,
                                'stream': stream, 'resolution': resolution, 'date': str(day)})
            tasks.append({'domain': domain, 'month': current.strftime('%Y%m'), 'items': items})
            current = next_month(current)
    if smoke:
        tasks = [t for t in tasks if t['month'] == '202604']
        for task in tasks:
            selected = {}
            for item in task['items']:
                selected.setdefault((item['product'], item['stream'], item['resolution']), item)
            task['items'] = list(selected.values())
    write(output/'plan.json', {'start': str(start), 'end': str(end), 'tasks': tasks, 'pins': pins,
        'createdAt': datetime.now(UTC).isoformat(), 'modelCaveat': 'Existing accepted-run snapshots; recent forcing revisions are not necessarily reflected in model states. No model reruns performed for this collection.'})
    write(output/'legends.json', legends())
    print(f'Planned {len(tasks)} month/domain tasks, {sum(len(t["items"]) for t in tasks)} source files', flush=True)


def checked(path, spec):
    if identity(path) != spec['pins'][str(path.relative_to(ROOT))]:
        raise ValueError(f'Source changed since planning: {path}')


def worker(output, task_id):
    spec = json.loads((output/'plan.json').read_text())
    task = spec['tasks'][task_id]
    domain = task['domain']
    tile_job, month_job = TABLES[domain]
    grid_path = ROOT/f'forcing/outputs/{domain}/retro/daily/2023/04/20230401.LDASIN_DOMAIN1.daily'
    checked(grid_path, spec)
    with netCDF4.Dataset(grid_path) as ds:
        lat, lon = ds['lat'][:], ds['lon'][:]
        x, y = mercator(corners(ds['lon_bnds'][:]), corners(ds['lat_bnds'][:]))
        forcing_mask = np.asarray(ds['subset_forcing_mask'][:], bool)
    mask_path = ROOT/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
    checked(mask_path, spec)
    with netCDF4.Dataset(mask_path) as ds:
        np.testing.assert_allclose(ds['lat'][:], lat, atol=1e-5)
        np.testing.assert_allclose(ds['lon'][:], lon, atol=1e-5)
        active = np.asarray(ds['model_mask'][:], bool)
    family = json.loads((ROOT/f'web/publications/pilot-5268948/{domain}/family.json').read_text())
    if hashlib.sha256(np.asarray([x, y], dtype='<f8').tobytes()).hexdigest() != family['gridSha256']:
        raise ValueError('Pilot grid changed')
    folder = output/domain/'images'/task['month'][:4]
    folder.mkdir(parents=True, exist_ok=True)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}/web-raster')
    scratch.mkdir(parents=True, exist_ok=True)
    tile_report_path = ROOT/f'nwm/status/{domain}/climatology/tiles-{tile_job}/acceptance.json'
    checked(tile_report_path, spec)
    accepted = json.loads(tile_report_path.read_text())
    assets = []
    palettes = legends()
    for item in task['items']:
        source = ROOT/item['source']; checked(source, spec)
        day = date.fromisoformat(item['date'])
        end = day+timedelta(days=1) if item['resolution'] == 'daily' else next_month(day)
        with netCDF4.Dataset(source) as ds:
            if item['product'] == 'nwm':
                values = derive(ds, day, active)
                ranks = np.full_like(values, np.nan)
                covered = np.zeros(active.shape, bool)
                doy = (date(2000, day.month, day.day)-date(2000, 1, 1)).days
                for tile in accepted['tiles']:
                    p = Path(tile['path']); checked(p, spec)
                    y0, y1, x0, x1 = tile['window']; yy, xx = slice(y0, y1), slice(x0, x1)
                    if covered[yy, xx].any():
                        raise ValueError('Overlapping tiles')
                    covered[yy, xx] = True
                    with netCDF4.Dataset(p) as q:
                        np.testing.assert_array_equal(q['percentile'][:], PERCENTILES)
                        np.testing.assert_allclose(q['lat'][:], lat[yy, xx], atol=1e-5)
                        np.testing.assert_allclose(q['lon'][:], lon[yy, xx], atol=1e-5)
                        for j, name in enumerate(('SOIL_WATER', 'SNEQV')):
                            ranks[j, yy, xx] = percentile_rank(values[j, yy, xx], q[name][doy],
                                                              swe_minimum=1. if j else None)
                    checked(p, spec)
                if not covered[active].all():
                    raise ValueError('Missing climatology tiles')
                for j in range(2):
                    eligible = active & np.isfinite(values[j])
                    if j:
                        eligible &= values[j] > 1
                    if not np.isfinite(ranks[j][eligible]).all():
                        raise ValueError('Missing eligible model ranks')
                fields = [('soil_water_percentile', ranks[0]), ('swe_percentile', ranks[1])]
            else:
                np.testing.assert_allclose(ds['lat'][:], lat, atol=1e-5)
                np.testing.assert_allclose(ds['lon'][:], lon, atol=1e-5)
                tv = ds['time']; b = ds['time_bounds']
                observed = netCDF4.num2date(b[0], getattr(b, 'units', tv.units), getattr(tv, 'calendar', 'standard'))
                if [str(t)[:19] for t in observed] != [str(d)+' 00:00:00' for d in (day, end)]:
                    raise ValueError('Source time bounds mismatch')
                if ds['T2D'].units != 'K' or ds['RAINRATE'].units not in ('kg/m^2/s', 'kg m-2 s-1'):
                    raise ValueError('Unexpected forcing units')
                vals = {n: np.ma.asarray(ds[n][0], dtype=float).filled(np.nan) for n in ('T2D', 'RAINRATE')}
                if item['resolution'] == 'daily':
                    fields = [('temperature', vals['T2D']-273.15), ('precipitation', vals['RAINRATE']*86400)]
                else:
                    table_dir = ROOT/f'forcing/analysis/{domain}/retro/monthly-climatology/wy1980-2025/job_{month_job}'
                    ma = json.loads((table_dir/'report.json').read_text())
                    table = ROOT/ma['output']; checked(table, spec)
                    fields = []
                    with netCDF4.Dataset(table) as q:
                        for name, var in [('T2D', 'temperature_percentile'), ('RAINRATE', 'precipitation_percentile')]:
                            rank = percentile_rank(vals[name], q[name][day.month-1], q['percentile'][:])
                            rank[np.asarray(q[name+'_sample_count'][day.month-1]) < ma['min_samples']] = np.nan
                            fields.append((var, rank))
                    checked(table, spec)
        for variable, values in fields:
            values[~forcing_mask] = np.nan
            label = day.strftime('%Y%m%d' if item['resolution'] == 'daily' else '%Y%m')
            name = f'{domain}_{item["product"]}_{item["stream"]}_{item["resolution"]}_{variable}_{label}.png'
            target = folder/name
            if target.exists():
                raise ValueError('Do not overwrite an existing test asset')
            raw = scratch/name
            validation = render(raw, values, x, y, family['projectedExtent'], [family['width'], family['height']], palettes[variable])
            encoding = save_indexed_png(raw, target)
            assets.append({**item, 'id': name[:-4], 'href': str(target.relative_to(output/domain)),
                'variable': variable, 'statistic': 'percentile_rank' if variable.endswith('_percentile') else 'value',
                'legendId': variable, 'mediaType': 'image/png', 'encoding': 'precolored-rgba', 'pngColorType': 'indexed',
                'paletteIndexBits': encoding['indexBits'], 'validInterval': {'start': str(day)+'T00:00:00Z',
                    'end': str(end)+'T00:00:00Z', 'closure': '[start,end)', 'label': 'start'},
                'sourceIdentity': spec['pins'][item['source']], 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), **validation})
        checked(source, spec)
        print(json.dumps({'domain': domain, **item, 'status': 'rendered'}), flush=True)
    (output/'shards').mkdir(exist_ok=True)
    write(output/'shards'/f'{task_id:03}.json', {'status': 'passed', 'assets': assets})


def finalize(output):
    spec = json.loads((output/'plan.json').read_text())
    assets = {d: [] for d in TABLES}
    for i, task in enumerate(spec['tasks']):
        shard = json.loads((output/'shards'/f'{i:03}.json').read_text())
        if shard['status'] != 'passed' or len(shard['assets']) != 2*len(task['items']):
            raise ValueError('Incomplete shard')
        assets[task['domain']].extend(shard['assets'])
    families = []
    for domain, rows in assets.items():
        for a in rows:
            if hashlib.sha256((output/domain/a['href']).read_bytes()).hexdigest() != a['sha256']:
                raise ValueError('Image checksum mismatch')
        family = json.loads((ROOT/f'web/publications/pilot-5268948/{domain}/family.json').read_text())
        family.update(status='web integration test collection; scientific visual review pending',
            revision=output.name, publishedAt=datetime.now(UTC).isoformat(),
            timeline='timeline.json', coverage='coverage.json', modelCaveat=spec['modelCaveat'])
        family['dimensions']['stream'] = ['retro', 'nrt']
        timeline = choose_timeline(rows)
        gaps = []
        for product, variables, resolution in [('forcing', ['temperature', 'precipitation'], 'daily'),
                ('nwm', ['soil_water_percentile', 'swe_percentile'], 'daily'),
                ('forcing', ['temperature_percentile', 'precipitation_percentile'], 'monthly')]:
            d = date.fromisoformat(spec['start']); last = date.fromisoformat(spec['end'])
            available = {(r['product'], r['variable'], r['date']) for r in timeline}
            while d <= last:
                if resolution == 'monthly' and next_month(d) > last+timedelta(days=1):
                    break
                for variable in variables:
                    if (product, variable, str(d)) not in available:
                        gaps.append({'product': product, 'variable': variable, 'date': str(d),
                                     'reason': 'No source file in frozen collection inventory'})
                d = d+timedelta(days=1) if resolution == 'daily' else next_month(d)
        write(output/domain/'availability.json', {'assets': rows})
        write(output/domain/'timeline.json', {'policy': 'retro preferred over nrt for each product/variable/interval; no forecast assets', 'entries': timeline})
        write(output/domain/'coverage.json', {'start': spec['start'], 'end': spec['end'], 'gaps': gaps})
        write(output/domain/'family.json', family)
        families.append({'id': family['id'], 'href': domain+'/family.json', 'images': len(rows), 'missingCombinations': len(gaps)})
    write(output/'acceptance.json', {'status': 'passed', 'browserValidated': False,
        'scope': 'Frozen source identities during each task, semantic/grid checks, exact indexed RGBA and final PNG checksums',
        'modelCaveat': spec['modelCaveat'], 'families': families})
    write(output/'catalog.json', {'families': families, 'revision': output.name, 'publishedAt': datetime.now(UTC).isoformat()})
    print(json.dumps(families), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['plan', 'worker', 'finalize'])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--start', type=date.fromisoformat, default=date(2025, 10, 1))
    p.add_argument('--end', type=date.fromisoformat, default=date(2026, 11, 11))
    p.add_argument('--task', type=int, default=int(os.environ.get('SLURM_ARRAY_TASK_ID', '0')))
    p.add_argument('--smoke', action='store_true', help='Plan one April 2026 source per product/stream/resolution/domain')
    a = p.parse_args()
    if a.action == 'plan':
        plan(a.output, a.start, a.end, a.smoke)
    elif a.action == 'worker':
        worker(a.output, a.task)
    else:
        finalize(a.output)
