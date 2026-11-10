"""Finalize an explicit, complete staged collection without changing production."""
import fcntl
import json
from contextlib import ExitStack
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json

from .station_catalog import IDENTITY, read_catalog, write_catalog
from .station_conversion import completed_tasks, digest, inside


def require_coverage(expected, groups):
    seen = set()
    for group in groups:
        if seen.intersection(group):
            raise ValueError('Duplicate task across campaigns')
        seen.update(group)
    if seen != set(expected):
        raise ValueError('Collection does not exactly cover eligible inventory')


def finalize(root, inventory, campaigns, catalog):
    root = Path(root).resolve()
    staging = root/'observations/work/station-migration'
    catalog = inside(root, catalog)
    if not catalog.is_relative_to(staging):
        raise ValueError('Staged catalog required; production cutover is separate')
    campaigns = [inside(root, p) for p in campaigns]
    if len(set(campaigns)) != len(campaigns):
        raise ValueError('Duplicate campaigns')
    if any(not p.is_relative_to(staging) for p in campaigns):
        raise ValueError('Staged campaigns required')
    inventory_path = inside(root, inventory)
    frozen = digest(inventory_path)
    report = json.loads(inventory_path.read_text())
    if report['schema'] != 'hydro_ops_station_migration_inventory_v1':
        raise ValueError('Unsupported inventory')
    eligible = {e['task_id']: e for e in report['entries'] if e['status'] == 'candidate'}
    with ExitStack() as stack:
        for campaign in sorted(campaigns):
            lock = stack.enter_context((campaign/'campaign.lock').open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        groups = [completed_tasks(root, p, inventory) for p in campaigns]
        require_coverage(eligible, groups)
        members = []
        for campaign, tasks in zip(campaigns, groups, strict=True):
            manifest_hash = digest(campaign/'campaign.json')
            entries = read_catalog(campaign/'catalog.json', verify=True)['entries']
            by_path = {inside(campaign, e['path']): e for e in entries}
            targets = {campaign/'stations'/f'{task}.nc' for task in tasks}
            if set(by_path) != targets:
                raise ValueError('Batch catalog does not match accepted tasks')
            for task in sorted(tasks):
                target = campaign/'stations'/f'{task}.nc'
                entry = by_path[target]
                receipt = json.loads((campaign/'receipts'/f'{task}.json').read_text())
                if (receipt['status'] != 'accepted' or receipt['task_id'] != task or
                        receipt['campaign_sha256'] != manifest_hash or
                        receipt['file_sha256'] != entry['sha256'] or
                        receipt['content_sha256'] != entry['content_sha256'] or
                        receipt['roundtrip'] != 'exact' or receipt['source_modified']):
                    raise ValueError('Conversion receipt mismatch')
                if any(str(entry[k]) != str(eligible[task][k]) for k in IDENTITY):
                    raise ValueError('Catalog identity differs from inventory')
                members.append(target)
        result = write_catalog(catalog, members, lock_root=catalog.parent/'collection-locks')
        read_catalog(catalog, verify=True)
        if digest(inventory_path) != frozen:
            raise ValueError('Inventory changed during finalization')
        acceptance = {'status': 'passed', 'members': len(members),
                      'inventory_sha256': frozen, 'catalog_sha256': digest(catalog),
                      'catalog': str(catalog.relative_to(root)),
                      'campaigns': [str(p.relative_to(root)) for p in campaigns],
                      'excluded': [e['task_id'] for e in report['entries'] if e['status'] != 'candidate'],
                      'production_modified': False, 'source_freshness_rechecked': False,
                      'publication': result}
        atomic_json(catalog.with_suffix('.acceptance.json'), acceptance)
        return acceptance
