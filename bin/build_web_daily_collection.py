"""Incremental, isolated daily web collection tests; no live transfer or cron mutation."""
import argparse
import copy
import json
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import build_web_raster_collection as renderer
import numpy as np
from mask_web_publication_mexico import country_mask, mask_image
from PIL import Image

from hydro_ops.forcing.web_contract import coverage, portable_identity
from hydro_ops.web.globus_transfer import atomic_json, digest, lock
from hydro_ops.web.publication import raster_inventory

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def key(row):
    return row['product'], row['resolution'], row['date']


def reusable(rows, item, pin, source):
    """A filesystem replacement alone need not cause expensive rerendering."""
    if len(rows) != 2 or any(r['source'] != item['source'] for r in rows):
        return False
    if all(r['sourceIdentity'] == portable_identity(pin) for r in rows):
        return True
    checksum = digest(source)
    return all(r['sourceSha256'] == checksum for r in rows)


def prepare(base, output, start, end, force_dates, receipts):
    if read(base / 'acceptance.json')['status'] != 'passed':
        raise ValueError('Accepted parent required')
    # Resolve the immutable pre-display renderer plan (v6 references v5).
    ancestor = base
    for _ in range(5):
        if (ancestor / 'plan.json').exists():
            break
        name = read(ancestor / 'acceptance.json')['sourcePublication']
        if Path(name).name != name:
            raise ValueError('Unsafe parent reference')
        ancestor = base.parent / name
    old_spec = read(ancestor / 'plan.json')
    output.mkdir(parents=True, exist_ok=False)
    receipt_pins = {}
    for path in receipts:
        path = path.resolve()
        if path.name == 'latest.json' or read(path).get('status') not in ('passed', 'complete'):
            raise ValueError('Job-specific successful upstream receipt required')
        receipt_pins[str(path.relative_to(ROOT))] = digest(path)
    renderer.plan(output / 'render', start, end, collection_v4=True, exclusive_streams=True)
    spec = read(output / 'render/plan.json')
    selected_sources = {item['source'] for task in spec['tasks'] for item in task['items']}
    for name, pin in spec['pins'].items():
        if name in selected_sources or name.startswith(('forcing/outputs/', 'nwm/outputs/')):
            continue
        if old_spec['pins'].get(name) != pin:
            raise ValueError('Static/climatology inputs changed; explicit full rebuild required')
    old_assets, reused, tasks = {}, [], []
    for domain in renderer.TABLES:
        family = read(base / domain / 'family.json')
        if family['processing']['codeSha256'] != spec['processing']['codeSha256']:
            raise ValueError('Renderer changed; explicit full rebuild required')
        old_assets[domain] = {}
        for row in read(base / domain / 'availability.json')['assets']:
            old_assets[domain].setdefault(key(row), []).append(row)
    for task in spec['tasks']:
        changed = []
        for item in task['items']:
            rows = old_assets[task['domain']].get(key(item), [])
            pin = spec['pins'][item['source']]
            if item['date'] not in force_dates and reusable(rows, item, pin, ROOT / item['source']):
                for original in rows:
                    row = copy.deepcopy(original)
                    row['sourceIdentity'] = portable_identity(pin)
                    reused.append({'domain': task['domain'], 'asset': row})
            else:
                changed.append(item)
        if changed:
            tasks.append(dict(task, items=changed))
    spec['tasks'] = tasks
    atomic_json(output / 'render/plan.json', spec)
    pins = {str(p.relative_to(ROOT)): digest(p) for p in
            [base / 'acceptance.json', base / 'display-policy.json', base / 'provenance/mexico.geojson',
             base / 'variables.json', base / 'legends.json', Path(__file__)]}
    atomic_json(output / 'cycle.json', {'status': 'planned', 'test': True,
        'base': str(base.relative_to(ROOT)), 'pins': pins, 'upstreamReceipts': receipt_pins,
        'renderPlanSha256': digest(output / 'render/plan.json'),
        'reused': reused, 'tasks': len(tasks), 'changedSources': sum(len(t['items']) for t in tasks),
        'forcedDates': sorted(force_dates), 'start': str(start), 'end': str(end)})
    print(json.dumps({'tasks': len(tasks), 'reusedImages': len(reused),
                      'changedSources': sum(len(t['items']) for t in tasks)}))


def checked(output):
    cycle = read(output / 'cycle.json')
    for name, checksum in (cycle['pins'] | cycle['upstreamReceipts']).items():
        if digest(ROOT / name) != checksum:
            raise ValueError('Pinned input/receipt/code changed')
    if digest(output / 'render/plan.json') != cycle['renderPlanSha256']:
        raise ValueError('Frozen render plan changed')
    return cycle


def worker(output, task_id):
    locks = output / 'locks'
    locks.mkdir(exist_ok=True)
    with lock(locks / f'task-{task_id}.lock'):
        render_task(output, task_id)


def render_task(output, task_id):
    cycle = checked(output)
    receipt = output / 'results' / f'{task_id}.json'
    if receipt.exists():
        result = read(receipt)
        for row in result['assets']:
            if digest(ROOT / result['folder'] / result['domain'] / row['href']) != row['sha256']:
                raise ValueError('Accepted task changed')
        return
    spec = read(output / 'render/plan.json')
    task = spec['tasks'][task_id]
    attempt = output / 'attempts' / f'{task_id}-{uuid.uuid4().hex[:12]}'
    attempt.mkdir(parents=True)
    atomic_json(attempt / 'plan.json', spec)
    renderer.worker(attempt, task_id)
    rows = read(attempt / 'shards' / f'{task_id:03}.json')['assets']
    base = ROOT / cycle['base']
    policy = read(base / 'display-policy.json')
    family = read(base / task['domain'] / 'family.json')
    geometry = read(base / policy['boundaryGeometry'])['features'][0]['geometry']
    mask = country_mask(geometry, family['projectedExtent'], family['width'], family['height'])
    for row in rows:
        if row['variable'] != 'soil_water_percentile':
            continue
        path = attempt / task['domain'] / row['href']
        target = path.with_suffix('.masked.png')
        encoding, counts = mask_image(path, target, mask)
        os.replace(target, path)
        row['preDisplaySha256'] = row['sha256']
        row['qualityCounts']['preDisplayImagePixels'] = copy.deepcopy(row['qualityCounts']['imagePixels'])
        row['qualityCounts']['imagePixels'] = counts
        row['qualityCounts']['countSemantics'] = policy['qualitySemantics']
        row['displayExclusion'] = {'country': 'Mexico', 'policy': '../display-policy.json'}
        row['visiblePixels'] = counts['visible']
        row['paletteIndexBits'] = encoding['indexBits']
        row['preDisplayImageStatus'] = row['imageStatus']
        if not counts['visible'] and counts['newlyHiddenVisible']:
            row['imageStatus'] = 'display_excluded'
        row['sha256'] = digest(path)
    checked(output)
    receipt.parent.mkdir(exist_ok=True)
    atomic_json(receipt, {'status': 'passed', 'domain': task['domain'], 'assets': rows,
                          'folder': str(attempt.relative_to(ROOT))})


def link(source, target, checksum):
    if digest(source) != checksum:
        raise ValueError('Asset checksum changed')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if digest(target) != checksum:
            raise ValueError('Conflicting output')
    else:
        os.link(source, target)  # Never edit shared PNG contents after linking.


def finalize(output):
    with lock(output / 'finalize.lock'):
        assemble(output)


def assemble(output):
    cycle = checked(output)
    spec = read(output / 'render/plan.json')
    base = ROOT / cycle['base']
    publication = output / 'publication'
    publication.mkdir(exist_ok=True)
    assets = {d: [] for d in renderer.TABLES}
    for entry in cycle['reused']:
        domain, row = entry['domain'], entry['asset']
        link(base / domain / row['href'], publication / domain / row['href'], row['sha256'])
        assets[domain].append(row)
    for i, task in enumerate(spec['tasks']):
        result = read(output / 'results' / f'{i}.json')
        if result['status'] != 'passed' or len(result['assets']) != 2 * len(task['items']):
            raise ValueError('Incomplete render task')
        for row in result['assets']:
            link(ROOT / result['folder'] / task['domain'] / row['href'],
                 publication / task['domain'] / row['href'], row['sha256'])
            assets[task['domain']].append(row)
    for name in ('variables.json', 'legends.json', 'display-policy.json', 'provenance/mexico.geojson'):
        target = publication / name
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(target, read(base / name))
    families = []
    for domain, rows in assets.items():
        family = read(base / domain / 'family.json')
        rows.sort(key=lambda r: r['id'])
        timeline = renderer.choose_timeline(rows)
        if any(len(t['availableAssets']) != 1 for t in timeline):
            raise ValueError('Overlapping streams')
        for row in rows:
            with Image.open(publication / domain / row['href']) as im:
                rgba = np.asarray(im.convert('RGBA'))
                if im.size != (family['width'], family['height']):
                    raise ValueError('Image geometry changed')
                if np.count_nonzero(rgba[..., 3]) != row['visiblePixels']:
                    raise ValueError('Visible count mismatch')
        months = sorted({d[:7] + '-01' for d in spec['requestedDailyDates']})
        requests = {(p, v, r): spec['requestedDailyDates'] if r == 'daily' else months
            for p, vv, r in [('nwm', ['soil_water_percentile', 'swe_percentile'], 'daily'),
                ('forcing', ['temperature', 'precipitation'], 'daily'),
                ('forcing', ['temperature_percentile', 'precipitation_percentile'], 'monthly')] for v in vv}
        cov = coverage(rows, requests, date.fromisoformat(cycle['end']))
        if cov['gaps']:
            raise ValueError('Incomplete requested coverage')
        family.update(revision=output.name, publishedAt=datetime.now(UTC).isoformat(),
            publication='Ordered in-place payload, metadata, then catalog updates; short mixed-version interval permitted.')
        for name, value in [('availability.json', {'assets': rows}), ('family.json', family),
                ('timeline.json', {'policy': family['streamPolicy'], 'entries': timeline}), ('coverage.json', cov)]:
            atomic_json(publication / domain / name, value)
        families.append({'id': family['id'], 'href': domain + '/family.json',
                         'images': len(rows), 'missingCombinations': 0})
    # Recheck all chosen source snapshots, including reused dates, before acceptance.
    for name, pin in spec['pins'].items():
        if renderer.identity(ROOT / name) != pin:
            raise ValueError('Input changed while building collection')
    checked(output)
    atomic_json(publication / 'catalog.json', {'families': families, 'revision': output.name,
                                             'publishedAt': datetime.now(UTC).isoformat()})
    acceptance = {'status': 'passed', 'test': True,
        'families': families, 'reusedImages': len(cycle['reused']), 'changedSources': cycle['changedSources'],
        'scope': 'Incremental isolated test; accepted-parent reuse, frozen sources, renderer validation, display mask, image counts, exclusive complete timeline.'}
    atomic_json(output / 'pending-acceptance.json', acceptance)
    atomic_json(publication / 'plan.json', spec)
    inventory = raster_inventory(publication, acceptance_path=output / 'pending-acceptance.json')
    atomic_json(output / 'inventory.json', inventory)
    atomic_json(publication / 'acceptance.json', acceptance)
    cycle['status'] = 'complete'
    atomic_json(output / 'cycle.json', cycle)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'worker', 'finalize'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base', type=Path)
    parser.add_argument('--start', type=date.fromisoformat)
    parser.add_argument('--end', type=date.fromisoformat)
    parser.add_argument('--force-date', action='append', default=[])
    parser.add_argument('--upstream-receipt', type=Path, action='append', default=[])
    parser.add_argument('--task', type=int, default=int(os.environ.get('SLURM_ARRAY_TASK_ID', '0')))
    args = parser.parse_args()
    if args.action == 'plan':
        if not args.base or not args.start or not args.end or args.start > args.end:
            parser.error('Valid --base, --start and --end required')
        prepare(args.base.resolve(), args.output.resolve(), args.start, args.end,
                set(args.force_date), args.upstream_receipt)
    elif args.action == 'worker':
        worker(args.output.resolve(), args.task)
    else:
        finalize(args.output.resolve())
