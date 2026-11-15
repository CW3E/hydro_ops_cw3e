"""Recoverable retirement of uninterpreted SWE combinations, without data loss."""
import json
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json

from .station_catalog import read_catalog
from .station_conversion import digest, inside
from .station_operations import active_catalog, copy_atomic, operation_lock, recover_pending

ARCHIVE = 'observations/archive/stations/cdec-swe-uninterpreted/v1'


def uninterpreted(entry):
    return (entry['provider'] == 'cdec' and entry['product'] == 'snow_water_equivalent'
            and (str(entry['sensor']), entry['resolution']) not in {('3', 'monthly'), ('82', 'daily')})


def run(root):
    root = Path(root).resolve()
    archive = root/ARCHIVE
    archive.mkdir(parents=True, exist_ok=True)
    with operation_lock(root):
        recover_pending(root)
        catalog = active_catalog(root)
        if catalog is None:
            raise ValueError('Active station collection required')
        journal = archive/'retirement.json'
        if not journal.exists():
            before = read_catalog(catalog)
            retired = [e for e in before['entries'] if uninterpreted(e)]
            if not retired:
                raise ValueError('No uninterpreted SWE entries to retire')
            atomic_json(archive/'active-catalog-before.json', before)
            atomic_json(journal, {'phase': 'copying', 'active_catalog': str(catalog.relative_to(root)),
                'before_sha256': digest(catalog), 'entries': retired,
                'reason': 'Unsupported CDEC SWE sensor/duration combinations; retained offline, not publishable',
                'restore_policy': 'Restore only after scientific review, under station operation lock; merge entries into current catalog, never overwrite with old snapshot'})
        plan = json.loads(journal.read_text())
        if plan['active_catalog'] != str(catalog.relative_to(root)):
            raise ValueError('Active catalog location changed')
        entries = plan['entries']
        if not entries or any(not uninterpreted(e) for e in entries):
            raise ValueError('Invalid retirement scope')
        for e in entries:
            source = inside(catalog.parent, e['path'])
            target = inside(archive, e['path'])
            if target.exists():
                if digest(target) != e['sha256']:
                    raise ValueError('Archive copy differs; no overwrite allowed')
            else:
                if digest(source) != e['sha256']:
                    raise ValueError('Source changed before retirement')
                copy_atomic(source, target, e['sha256'])
        # Every archive file must verify before any active membership is changed.
        before = json.loads((archive/'active-catalog-before.json').read_text())
        atomic_json(archive/'catalog.json', {**before, 'entries': entries,
                    'scope': 'Offline uninterpreted CDEC SWE; not eligible for update/publication'})
        read_catalog(archive/'catalog.json', verify=True)
        current = read_catalog(catalog)
        paths = {e['path'] for e in entries}
        present = paths & {e['path'] for e in current['entries']}
        if present:
            if digest(catalog) != plan['before_sha256']:
                raise ValueError('Active catalog changed before retirement; review required')
            atomic_json(catalog, {**current, 'entries': [e for e in current['entries'] if e['path'] not in paths]})
        plan['phase'] = 'catalog_retired'
        atomic_json(journal, plan)
        # Exact-file cleanup only after catalog retirement and independent copy checks.
        for e in entries:
            source = inside(catalog.parent, e['path'])
            target = inside(archive, e['path'])
            if digest(target) != e['sha256']:
                raise ValueError('Archive verification failed; retain remaining originals')
            if source.exists():
                if digest(source) != e['sha256']:
                    raise ValueError('Original changed; refusing removal')
                source.unlink()
        remaining = read_catalog(catalog)
        if any(uninterpreted(e) for e in remaining['entries']):
            raise ValueError('Unsupported SWE remains in active catalog')
        plan.update(phase='complete', archived_files=len(entries),
                    archived_bytes=sum(e['bytes'] for e in entries),
                    active_catalog_sha256=digest(catalog), active_members=len(remaining['entries']))
        atomic_json(journal, plan)
        atomic_json(archive/'acceptance.json', {k: v for k, v in plan.items() if k != 'entries'})
        return {k: v for k, v in plan.items() if k != 'entries'}
