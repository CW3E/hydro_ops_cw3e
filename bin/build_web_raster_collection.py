"""Plan, render month shards, and finalize an immutable multi-stream web test collection."""
import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import netCDF4
import numpy as np
from benchmark_nwm_history_stream import derive
from PIL import Image
from publish_web_raster_pilot import identity, write

from hydro_ops.forcing.web_contract import (
    coverage,
    decoded_field,
    physical_values,
    portable_identity,
)
from hydro_ops.forcing.web_contract import variables as variable_definitions
from hydro_ops.forcing.web_rasters import (
    corners,
    legends,
    legends_v3,
    mercator,
    render,
    render_v3,
    save_indexed_png,
)
from hydro_ops.wrf_hydro.climatology import PERCENTILES, percentile_rank, statistics

ROOT = Path(__file__).resolve().parents[1]
TABLES = {'cnrfc': (5260669, 5263647), 'ucrb': (5260677, 5263648)}
PILOT_DAYS = ['2015-04-01', '2023-04-01', '2025-09-30', '2025-12-13', '2026-04-12', '2026-09-15']


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


def select_exclusive_streams(items):
    selected = {}
    for item in items:
        key = item['product'], item['resolution'], item['date']
        if key not in selected or item['stream'] == 'retro':
            selected[key] = item
    return list(selected.values())


def plan(output, start, end, smoke=False, pilot_v3=False, collection_v4=False, exclusive_streams=False):
    if collection_v4 and (smoke or pilot_v3):
        raise ValueError('Use --collection-v4 independently')
    if smoke and pilot_v3:
        raise ValueError('Use the representative v3 selection without --smoke')
    if pilot_v3:
        start = date.fromisoformat(min(PILOT_DAYS))
    output.mkdir(parents=True, exist_ok=False)
    tasks, pins = [], {}
    for domain, (tile_job, month_job) in TABLES.items():
        tile_path = ROOT/f'nwm/status/{domain}/climatology/tiles-{tile_job}/acceptance.json'
        accepted = json.loads(tile_path.read_text())
        if accepted['status'] != 'passed' or accepted['source_model_campaign'] != 'repaired_rain_retro_v1':
            raise ValueError('Corrected accepted climatology required')
        paths = [tile_path, ROOT/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc',
                 ROOT/f'web/publications/pilot-5268948/{domain}/family.json']
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
        months = sorted({date.fromisoformat(d).replace(day=1) for d in PILOT_DAYS}) if pilot_v3 else []
        current = start.replace(day=1)
        if not pilot_v3:
            while current <= end:
                months.append(current); current = next_month(current)
        for current in months:
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
                            if pilot_v3 and resolution == 'daily' and str(day) not in PILOT_DAYS:
                                continue
                            pins[str(p.relative_to(ROOT))] = identity(p)
                            items.append({'source': str(p.relative_to(ROOT)), 'product': product,
                                'stream': stream, 'resolution': resolution, 'date': str(day)})
            if exclusive_streams:
                items = select_exclusive_streams(items)
            tasks.append({'domain': domain, 'month': current.strftime('%Y%m'), 'items': items})
    if smoke:
        tasks = [t for t in tasks if t['month'] == '202604']
        for task in tasks:
            selected = {}
            for item in task['items']:
                selected.setdefault((item['product'], item['stream'], item['resolution']), item)
            task['items'] = list(selected.values())
    if collection_v4:
        tasks = [dict(t, items=t['items'][i:i+8]) for t in tasks for i in range(0, len(t['items']), 8)]
    code_paths = [Path(__file__), ROOT/'src/hydro_ops/forcing/web_rasters.py', ROOT/'src/hydro_ops/forcing/web_contract.py',
                  ROOT/'src/hydro_ops/wrf_hydro/climatology.py', ROOT/'bin/benchmark_nwm_history_stream.py']
    processing = {'gitCommit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  'codeSha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths},
                  'model': 'WRF-Hydro 5.4.0 with Hydro Ops daily I/O and local configuration; not an unmodified operational NOAA NWM forecast'}
    write(output/'plan.json', {'start': str(start), 'end': str(end), 'tasks': tasks, 'pins': pins,
        'pilotV3': pilot_v3 or collection_v4, 'collectionV4': collection_v4, 'processing': processing,
        'exclusiveStreams': exclusive_streams,
        'requestedDailyDates': [str(start+timedelta(days=i)) for i in range((end-start).days+1)] if collection_v4 else PILOT_DAYS if pilot_v3 else None,
        'createdAt': datetime.now(UTC).isoformat(), 'modelCaveat': 'Existing accepted-run snapshots; recent forcing revisions are not necessarily reflected in model states. No model reruns performed for this collection.'})
    palettes = legends_v3() if pilot_v3 or collection_v4 else legends()
    if collection_v4:
        palettes['temperature']['thresholdUnit'] = 'Cel'
        for name, legend in palettes.items():
            if name.endswith('_percentile'):
                legend.update(thresholdUnit='1', displayUnitLabel='%', numericScale=variable_definitions()['variables'][name]['numericScale'])
    write(output/'legends.json', palettes)
    if pilot_v3 or collection_v4:
        write(output/'variables.json', variable_definitions())
    print(f'Planned {len(tasks)} month/domain tasks, {sum(len(t["items"]) for t in tasks)} source files', flush=True)


def checked(path, spec):
    if identity(path) != spec['pins'][str(path.relative_to(ROOT))]:
        raise ValueError(f'Source changed since planning: {path}')


def worker(output, task_id):
    spec = json.loads((output/'plan.json').read_text())
    v3 = spec.get('pilotV3', False)
    if v3:
        for name, checksum in spec['processing']['codeSha256'].items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != checksum:
                raise ValueError('Publisher code changed after planning')
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
    family_path = ROOT/f'web/publications/pilot-5268948/{domain}/family.json'
    if v3:
        checked(family_path, spec)
    family = json.loads(family_path.read_text())
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
    palettes = legends_v3() if v3 else legends()
    for item in task['items']:
        source = ROOT/item['source']; checked(source, spec)
        day = date.fromisoformat(item['date'])
        end = day+timedelta(days=1) if item['resolution'] == 'daily' else next_month(day)
        quality, source_attributes = {}, {}
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest() if v3 else None
        with netCDF4.Dataset(source) as ds:
            if spec.get('collectionV4'):
                expected_count = 24 if item['resolution'] == 'daily' else (end-day).days
                if int(getattr(ds, 'aggregation_sample_count', -1)) not in (
                        [expected_count] if item['resolution'] == 'daily' else [expected_count, expected_count*24]):
                    raise ValueError('Incomplete or undocumented aggregate sample count')
            if v3:
                fields_to_record = ('SOIL_M', 'SNEQV') if item['product'] == 'nwm' else ('T2D', 'RAINRATE')
                source_attributes = {n: {k: str(ds[n].getncattr(k)) for k in ds[n].ncattrs()
                    if k in ('_FillValue', 'missing_value', 'valid_range', 'valid_min', 'valid_max', 'scale_factor', 'add_offset', 'units', 'cell_methods')}
                    for n in fields_to_record}
            if item['product'] == 'nwm':
                values = derive(ds, day, active)
                if v3:
                    soil = decoded_field(ds['SOIL_M'], 0)
                    snow = decoded_field(ds['SNEQV'], 0)
                    soil_bad = (np.isfinite(soil) & ((soil < 0) | (soil > 1))).any(axis=1)
                    quality['soil_water_percentile'] = {'source_valid': np.isfinite(values[0]), 'invalid': soil_bad}
                    quality['swe_percentile'] = {'source_valid': np.isfinite(values[1]),
                        'invalid': np.isfinite(snow) & (snow < 0), 'suppressed': np.isfinite(values[1]) & (values[1] <= 1)}
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
                if v3:
                    for n, original in vals.items():
                        vals[n], bad = physical_values(original, n)
                        var = 'temperature' if n == 'T2D' else 'precipitation'
                        if item['resolution'] == 'monthly':
                            var += '_percentile'
                        quality[var] = {'source_valid': np.isfinite(original), 'invalid': bad}
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
            if v3:
                validation = render_v3(raw, values, x, y, family['projectedExtent'], [family['width'], family['height']],
                    palettes[variable], domain_mask=forcing_mask & active if item['product'] == 'nwm' else forcing_mask,
                    **quality[variable])
            else:
                validation = render(raw, values, x, y, family['projectedExtent'], [family['width'], family['height']], palettes[variable])
            encoding = save_indexed_png(raw, target)
            assets.append({**item, 'id': name[:-4], 'href': str(target.relative_to(output/domain)),
                'variable': variable, 'statistic': 'percentile_rank' if variable.endswith('_percentile') else 'value',
                'legendId': variable, 'mediaType': 'image/png', 'encoding': 'precolored-rgba', 'pngColorType': 'indexed',
                'paletteIndexBits': encoding['indexBits'], 'validInterval': {'start': str(day)+'T00:00:00Z',
                    'end': str(end)+'T00:00:00Z', 'closure': '[start,end)', 'label': 'start'},
                'sourceIdentity': portable_identity(spec['pins'][item['source']]) if v3 else spec['pins'][item['source']],
                **({'sourceSha256': source_sha, 'sourceReferenceRole': 'provenance-only repository-relative identifier, not a downloadable URL',
                    'sourceAttributes': source_attributes, 'variableDefinition': '../variables.json#/variables/'+variable,
                    'aggregation': variable_definitions()['variables'][variable]['aggregation'],
                    'climatology': {'dailyTileAcceptance': str(tile_report_path.relative_to(ROOT)),
                        'dailyTileAcceptanceSha256': hashlib.sha256(tile_report_path.read_bytes()).hexdigest(),
                        'monthlyTableJob': str(month_job),
                        **({'monthlyTable': str(table.relative_to(ROOT)), 'monthlyTableSha256': hashlib.sha256(table.read_bytes()).hexdigest()}
                           if item['resolution'] == 'monthly' else {})} if variable.endswith('_percentile') else None} if v3 else {}),
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), **validation})
        checked(source, spec)
        print(json.dumps({'domain': domain, **item, 'status': 'rendered'}), flush=True)
    (output/'shards').mkdir(exist_ok=True)
    write(output/'shards'/f'{task_id:03}.json', {'status': 'passed', 'assets': assets})


def validation_fixtures(output):
    folder = output/'validation-fixtures'
    folder.mkdir(exist_ok=True)
    x, y = np.meshgrid(np.arange(4), np.arange(2))
    report = render_v3(folder/'synthetic-quality.png', np.array([[np.nan, np.nan, 90.]]),
        x, y, [0, 0, 3, 1], [300, 100], legends_v3()['swe_percentile'],
        domain_mask=np.ones((1, 3), bool), source_valid=np.ones((1, 3), bool),
        invalid=np.array([[True, False, False]]))
    assert report['qualityCounts']['sourceCells']['invalidEstimate'] == 1
    assert report['qualityCounts']['sourceCells']['statisticUnavailable'] == 1
    windows = [np.arange(4) for _ in range(366)]
    windows[58], windows[60] = np.array([0, 1]), np.array([2, 3])
    q, count = statistics(np.arange(4.).reshape(4, 1, 1), windows, [50])
    assert q[59, 0, 0, 0] == (q[58, 0, 0, 0]+q[60, 0, 0, 0])/2 and count[59, 0, 0] == -1
    write(folder/'report.json', {'synthetic': True, 'status': 'passed',
        'warning': 'Validation fixtures only; not georeferenced scientific data, excluded from all families/timelines.',
        'quality': report, 'february29': {'threshold': float(q[59, 0, 0, 0]),
        'february28': float(q[58, 0, 0, 0]), 'march1': float(q[60, 0, 0, 0]), 'sampleCount': -1}})


def finalize(output):
    spec = json.loads((output/'plan.json').read_text())
    v3 = spec.get('pilotV3', False)
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
            if v3:
                with Image.open(output/domain/a['href']) as im:
                    rgba = np.asarray(im.convert('RGBA'))
                    allowed = {tuple(bytes.fromhex(c[1:])) for c in legends_v3()[a['variable']]['colors']} | {(0, 0, 0, 0)}
                    if not set(map(tuple, np.unique(rgba.reshape(-1, 4), axis=0))) <= allowed:
                        raise ValueError('Undeclared pixel color, including transparent RGB')
                    counts = a['qualityCounts']['imagePixels']
                    if counts['total'] != im.width*im.height or counts['visible'] != np.count_nonzero(rgba[..., 3]):
                        raise ValueError('Pixel quality count mismatch')
                for counts in a['qualityCounts'].values():
                    if not isinstance(counts, dict):
                        continue
                    if counts['total'] != sum(counts[k] for k in ('outsideDomain', 'missingEstimate', 'invalidEstimate',
                            'suppressedBySweCutoff', 'statisticUnavailable', 'validIntentionallyHidden', 'visible')):
                        raise ValueError('Quality categories do not partition domain')
        family = json.loads((ROOT/f'web/publications/pilot-5268948/{domain}/family.json').read_text())
        family.update(status='web integration test collection; scientific visual review pending',
            revision=output.name, publishedAt=datetime.now(UTC).isoformat(),
            timeline='timeline.json', coverage='coverage.json', modelCaveat=spec['modelCaveat'])
        family['dimensions']['stream'] = ['retro', 'nrt']
        if v3:
            family.update(conventionVersion='0.3', title=f'Hydro Ops {domain.upper()} forcing and land-state pilot',
                variables='../variables.json', processing=spec['processing'],
                contact={'organization': 'CW3E Hydro Ops', 'publicContact': None,
                         'status': 'Not supplied; confirm with project maintainer before public release'},
                attribution=['CW3E Hydro Ops processing', 'NCAR WRF-Hydro model',
                             'NASA/NOAA NLDAS-2; NOAA HRRR/GFS/Stage-IV/MRMS; PRISM Climate Group forcing inputs'],
                reuseTerms='Evaluation only. This pilot grants no permission for operational use or redistribution; obtain project approval and applicable source-data terms before public release.',
                publicReleaseReady=False, displayResampling='nearest-neighbor',
                sourceReferences={'root': 'hydro_ops project repository', 'role': 'provenance-only, not manifest-relative URLs',
                    'sourceIdentity': 'Named filesystem snapshot fields, all decimal strings. inode identifies a local file; sizeBytes is bytes; mtimeNanoseconds is Unix-epoch nanoseconds. Nonportable identity is diagnostic only; sourceSha256 is the durable content identifier.'},
                publication='Immutable revision directory. Deploy under a revision-specific URL; stable entry point must link to that release. Do not replace v2 in place.')
        if spec.get('collectionV4'):
            family['conventionVersion'] = '0.4'
            family['statistics'] = {'authoritativeDefinitions': '../variables.json',
                                    'note': 'Valid SWE below cutoff has intentionally suppressed ranks, not missing source estimates.'}
        timeline = choose_timeline(rows)
        if spec.get('exclusiveStreams'):
            if any(len(row['availableAssets']) != 1 for row in timeline):
                raise ValueError('Exclusive publication contains overlapping stream assets')
            family['streamPolicy'] = 'Exactly one asset per product/variable/interval: retro when present, otherwise nrt. Selection is independent for each product/resolution.'
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
        write(output/domain/'timeline.json', {'policy': family.get('streamPolicy', 'retro preferred over nrt for each product/variable/interval; no forecast assets'), 'entries': timeline})
        if v3:
            wanted_months = sorted({d[:7]+'-01' for d in spec['requestedDailyDates']} | {spec['end'][:7]+'-01'})
            requests = {(p, v, r): spec['requestedDailyDates'] if r == 'daily' else wanted_months
                for p, vv, r in [('forcing', ['temperature', 'precipitation'], 'daily'),
                    ('nwm', ['soil_water_percentile', 'swe_percentile'], 'daily'),
                    ('forcing', ['temperature_percentile', 'precipitation_percentile'], 'monthly')] for v in vv}
            cov = coverage(rows, requests, date.fromisoformat(spec['end']))
            if spec.get('collectionV4'):
                cov['scope'] = 'Continuous daily coverage over the requested interval; complete calendar months only.'
                if cov['gaps']:
                    raise ValueError(f'Incomplete continuous coverage for {domain}')
            gaps = cov['gaps']
            write(output/domain/'coverage.json', cov)
        else:
            write(output/domain/'coverage.json', {'start': spec['start'], 'end': spec['end'], 'gaps': gaps})
        write(output/domain/'family.json', family)
        families.append({'id': family['id'], 'href': domain+'/family.json', 'images': len(rows), 'missingCombinations': len(gaps)})
    if spec.get('collectionV4'):
        validation_fixtures(output)
    write(output/'acceptance.json', {'status': 'passed', 'browserValidated': False,
        'scope': 'Frozen source identities during each task, semantic/grid checks, exact indexed RGBA and final PNG checksums; v3 also checks every decoded RGBA color and quality-count partitions',
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
    p.add_argument('--pilot-v3', action='store_true', help='Representative v3 samples with full scientific/quality contract')
    p.add_argument('--collection-v4', action='store_true', help='Continuous collection with refined metadata')
    p.add_argument('--exclusive-streams', action='store_true', help='Publish retro when present, otherwise nrt; never both for one product/interval')
    a = p.parse_args()
    if a.action == 'plan':
        plan(a.output, a.start, a.end, a.smoke, a.pilot_v3, a.collection_v4, a.exclusive_streams)
    elif a.action == 'worker':
        worker(a.output, a.task)
    else:
        finalize(a.output)
