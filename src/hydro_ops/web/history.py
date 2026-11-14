"""Merge accepted refresh windows without shrinking previously published coverage."""
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.web_contract import coverage

from .globus_transfer import atomic_json, digest
from .publication import raster_inventory, relative


def read(path):
    return json.loads(path.read_text())


def logical_key(row):
    return tuple(row[k] for k in ('product', 'variable', 'resolution', 'date'))


def index(rows):
    result = {}
    ids, paths = set(), set()
    for row in rows:
        key = logical_key(row)
        if key in result or row['id'] in ids or row['href'] in paths:
            raise ValueError('Duplicate logical asset, identifier or image path')
        if row['stream'] not in ('retro', 'nrt'):
            raise ValueError('Unsupported publication stream')
        relative(row['href'])
        result[key] = row
        ids.add(row['id'])
        paths.add(row['href'])
    return result


def select(old_rows, refreshed_rows):
    old, refresh = index(old_rows), index(refreshed_rows)
    for key in old.keys() & refresh.keys():
        if old[key]['stream'] == 'retro' and refresh[key]['stream'] != 'retro':
            raise ValueError('Refusing automatic Retro-to-NRT downgrade')
        if old[key]['validInterval'] != refresh[key]['validInterval']:
            raise ValueError('Replacement interval bounds changed')
    merged = old | refresh
    index(list(merged.values()))
    return merged, {'retainedImages': len(old.keys() - refresh.keys()),
        'replacedImages': len(old.keys() & refresh.keys()), 'addedImages': len(refresh.keys() - old.keys()),
        'changedExistingImages': sum(old[k]['sha256'] != refresh[k]['sha256']
                                    for k in old.keys() & refresh.keys()),
        'lostLogicalAssets': len(old.keys() - merged.keys())}


def continuous_coverage(rows):
    daily = [date.fromisoformat(r['date']) for r in rows if r['resolution'] == 'daily']
    if not daily:
        raise ValueError('Daily coverage required')
    first, last = min(daily), max(daily)
    days = [str(first + timedelta(days=i)) for i in range((last - first).days + 1)]
    months = sorted({d[:7] + '-01' for d in days if d[:7] + '-01' >= str(first)})
    requests = {(p, v, resolution): days if resolution == 'daily' else months
        for p, variables, resolution in [('forcing', ['temperature', 'precipitation'], 'daily'),
            ('nwm', ['soil_water_percentile', 'swe_percentile'], 'daily'),
            ('forcing', ['temperature_percentile', 'precipitation_percentile'], 'monthly')]
        for v in variables}
    result = coverage(rows, requests, last)
    if result['gaps']:
        raise ValueError('Merged full history has coverage gaps')
    result['scope'] = 'Continuous full-history daily coverage; complete calendar months only.'
    return result


def merge(base, refresh, output):
    base, refresh, output = (Path(p).resolve() for p in (base, refresh, output))
    if output.exists():
        raise ValueError('Use a fresh immutable merge output')
    inventories = [raster_inventory(p) for p in (base, refresh)]
    render_plan = read(refresh / 'plan.json')
    render_plan_sha = digest(refresh / 'plan.json')
    catalogs = [read(p / 'catalog.json') for p in (base, refresh)]
    families = [{f['id']: f for f in c['families']} for c in catalogs]
    if (any(len(f) != len(c['families']) for f, c in zip(families, catalogs, strict=True))
            or set(families[0]) != set(families[1])):
        raise ValueError('Refresh must contain every existing family')
    for name in ('variables.json', 'legends.json', 'display-policy.json'):
        if read(base / name) != read(refresh / name):
            raise ValueError('Publication definitions/display policy changed; explicit migration required')
    output.mkdir(parents=True)
    report = {'status': 'building', 'test': True, 'base': str(base), 'refresh': str(refresh),
              'families': [], 'scope': 'Preserve accepted historical assets; update only supplied logical keys.'}
    published = []
    for fid, entry in families[0].items():
        path = relative(entry['href'])
        if families[1][fid]['href'] != path:
            raise ValueError('Family path changed')
        domain = Path(path).parent
        original, latest = read(base / path), read(refresh / path)
        for field in ('id', 'domain', 'pixelProjection', 'projectedExtent', 'placement', 'width', 'height'):
            if original[field] != latest[field]:
                raise ValueError('Family grid/placement changed')
        old_rows = read(base / domain / relative(original['availability']))['assets']
        new_rows = read(refresh / domain / relative(latest['availability']))['assets']
        rows, counts = select(old_rows, new_rows)
        new_keys = set(index(new_rows))
        ordered = sorted(rows.values(), key=lambda r: r['id'])
        cov = continuous_coverage(ordered)
        for key, row in rows.items():
            source = (refresh if key in new_keys else base) / domain / row['href']
            if digest(source) != row['sha256']:
                raise ValueError('Input PNG changed during merge')
            target = output / domain / row['href']
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)  # Immutable PNGs only; never edit linked contents.
        latest.update(revision=output.name, publishedAt=datetime.now(UTC).isoformat(),
            historyPolicy='Retain accepted snapshots outside the supplied refresh; receipt validation covers the refreshed interval only.')
        timeline = [{'product': r['product'], 'variable': r['variable'], 'resolution': r['resolution'],
                     'date': r['date'], 'preferredAsset': r['id'], 'availableAssets': [r['id']]} for r in ordered]
        for name, data in [('family.json', latest), ('availability.json', {'assets': ordered}),
                           ('timeline.json', {'policy': latest['streamPolicy'], 'entries': timeline}),
                           ('coverage.json', cov)]:
            atomic_json(output / domain / name, data)
        published.append({'id': fid, 'href': path, 'images': len(ordered), 'missingCombinations': 0})
        report['families'].append({'id': fid, **counts, 'images': len(ordered), 'cutoff': cov['cutoff']})
    policy = read(base / 'display-policy.json')
    for name in ('variables.json', 'legends.json', 'display-policy.json', relative(policy['boundaryGeometry'])):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(target, read(base / name))
    atomic_json(output / 'catalog.json', {'families': published, 'revision': output.name,
                                         'publishedAt': datetime.now(UTC).isoformat()})
    atomic_json(output / 'plan.json', render_plan)  # Private renderer/static pins for the next cycle.
    # Source metadata and assets must still match the inventories accepted on entry.
    for folder, inventory in zip((base, refresh), inventories, strict=True):
        for row in inventory['files']:
            if digest(folder / row['path']) != row['sha256']:
                raise ValueError('Source publication changed during merge')
        if digest(folder / 'acceptance.json') != inventory['acceptance_sha256']:
            raise ValueError('Source acceptance changed during merge')
    if digest(refresh / 'plan.json') != render_plan_sha:
        raise ValueError('Refresh renderer plan changed during merge')
    report['status'] = 'passed'
    pending = output.parent / (output.name + '-pending-acceptance.json')
    atomic_json(pending, report)
    inventory = raster_inventory(output, acceptance_path=pending)
    atomic_json(output.parent / (output.name + '-inventory.json'), inventory)
    atomic_json(output / 'acceptance.json', report)
    return report
