"""Bounded staged conversions with frozen inputs and resumable acceptance."""
import fcntl
import hashlib
import json
import multiprocessing
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json

from .station_adapters import archive_table
from .station_catalog import read_catalog, write_catalog
from .station_migration import inspect
from .station_store import read_station, write_station
from .station_swe import swe_archive_table


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def inside(root, name):
    path = (Path(root)/name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Path escapes project')
    return path


def check_entry(root, entry):
    identity = {k: entry[k] for k in ('provider', 'product', 'station_id', 'sensor', 'resolution', 'source')}
    current = inspect(root, identity)
    if current != entry:
        raise ValueError('Source/evidence changed since inventory; replan required')


def select_batch(report, *, size, excluded=()):
    """Select a deterministic, balanced next batch; never include blocked entries."""
    if not 1 <= size <= 128:
        raise ValueError('Batch size must be 1–128')
    excluded = set(excluded)
    groups = {}
    seen = set()
    for entry in report['entries']:
        task = entry['task_id']
        if task in seen:
            raise ValueError('Duplicate inventory task')
        seen.add(task)
        if entry['status'] == 'candidate' and task not in excluded:
            key = tuple(entry[k] for k in ('provider', 'product', 'sensor', 'resolution'))
            groups.setdefault(key, []).append(task)
    selected = []
    for index in range(size):
        for group in sorted(groups):
            if index < len(groups[group]) and len(selected) < size:
                selected.append(groups[group][index])
    if not selected:
        raise ValueError('No remaining candidates')
    return selected


def completed_tasks(root, campaign, inventory_path):
    """Exclude only tasks listed as accepted in a passed matching campaign.

    This is selection evidence, not a fresh payload audit of the earlier batch.
    """
    campaign = inside(root, campaign)
    manifest = json.loads((campaign/'campaign.json').read_text())
    receipt = json.loads((campaign/'acceptance.json').read_text())
    tasks = {e['task_id'] for e in manifest['entries']}
    if manifest['inventory_sha256'] != digest(inside(root, inventory_path)):
        raise ValueError('Excluded campaign belongs to another inventory')
    if receipt['status'] != 'passed' or not receipt['passes']:
        raise ValueError('Excluded campaign has not passed')
    last = receipt['passes'][-1]
    if (len(last) != len(tasks) or {r['task_id'] for r in last} != tasks or
            any(r['status'] not in ('accepted', 'skipped_verified') for r in last)):
        raise ValueError('Excluded campaign task acceptance is incomplete')
    return tasks


def prepare(root, campaign, inventory_path, fnf_inventory, swe_inventory, *, task_ids=None):
    root, campaign = Path(root).resolve(), Path(campaign).resolve()
    if not campaign.is_relative_to(root/'observations/work/station-migration'):
        raise ValueError('Campaign must remain in staged station-migration workspace')
    inventory_path = inside(root, inventory_path)
    report = json.loads(inventory_path.read_text())
    if report['schema'] != 'hydro_ops_station_migration_inventory_v1':
        raise ValueError('Unsupported inventory')
    selected = report['proposed_pilot']['task_ids'] if task_ids is None else task_ids
    if not 1 <= len(selected) <= 128 or len(set(selected)) != len(selected):
        raise ValueError('Conversion requires 1–128 unique tasks')
    entries = [e for e in report['entries'] if e['task_id'] in selected]
    if len(entries) != len(selected) or any(e['status'] != 'candidate' for e in entries):
        raise ValueError('Selected tasks must be unique candidates')
    for entry in entries:
        if not re.fullmatch('[0-9a-f]{24}', entry['task_id']):
            raise ValueError('Invalid task identifier')
    metadata = {}
    for name, path in [('fnf', fnf_inventory), ('swe', swe_inventory)]:
        path = inside(root, path)
        metadata[name] = {'path': str(path.relative_to(root)), 'sha256': digest(path)}
    manifest = {'schema': 'hydro_ops_station_conversion_campaign_v1',
                'inventory_path': str(inventory_path.relative_to(root)),
                'inventory_sha256': digest(inventory_path), 'metadata': metadata, 'entries': entries}
    campaign.mkdir(parents=True, exist_ok=True)
    path = campaign/'campaign.json'
    if path.exists():
        if json.loads(path.read_text()) != manifest:
            raise ValueError('Campaign inputs differ; use a new campaign')
    else:
        atomic_json(path, manifest)
    return manifest


def convert(arguments):
    root, campaign, entry, metadata, campaign_sha = arguments
    root, campaign = Path(root), Path(campaign)
    started = time.monotonic()
    task = entry['task_id']
    target = campaign/'stations'/f'{task}.nc'
    acceptance = campaign/'receipts'/f'{task}.json'
    result = {'task_id': task, 'station_id': entry['station_id'], 'status': 'failed'}
    try:
        check_entry(root, entry)
        for item in metadata.values():
            if digest(inside(root, item['path'])) != item['sha256']:
                raise ValueError('Station inventory metadata changed')
        if acceptance.exists():
            receipt = json.loads(acceptance.read_text())
            if (receipt['status'] != 'accepted' or receipt['campaign_sha256'] != campaign_sha or
                    receipt['task_id'] != task or digest(target) != receipt['file_sha256']):
                raise ValueError('Accepted output/evidence mismatch; manual review required')
            for source in receipt['sources']:
                if digest(inside(root, source['path'])) != source['sha256']:
                    raise ValueError('Accepted source payload changed; replan required')
            table = read_station(target)
            if table.signature() != receipt['content_sha256']:
                raise ValueError('Accepted payload changed')
            check_entry(root, entry)
            result.update(status='skipped_verified', rows=len(table.rows), bytes=target.stat().st_size)
        else:
            if entry['product'] == 'snow_water_equivalent':
                table = swe_archive_table(root, entry['station_id'], int(entry['sensor']), entry['resolution'],
                                          inventory=Path(metadata['swe']['path']))
            else:
                table = archive_table(root, entry['provider'], entry['station_id'], entry['resolution'],
                                      cdec_inventory=Path(metadata['fnf']['path']))
            check_entry(root, entry)
            # An orphan may be from a crash after rename. Recover only an exact
            # match; never overwrite a different orphan and hide evidence.
            if target.exists():
                if read_station(target) != table:
                    raise ValueError('Unaccepted output differs from source; manual review required')
            else:
                write_station(target, table, lock_root=campaign/'locks')
            if read_station(target) != table:
                raise ValueError('Round-trip differs from source')
            for source in table.metadata['provenance']:
                if digest(inside(root, source['path'])) != source['sha256']:
                    raise ValueError('Source changed during publication')
            check_entry(root, entry)
            receipt = {'status': 'accepted', 'campaign_sha256': campaign_sha, 'task_id': task,
                       'content_sha256': table.signature(), 'file_sha256': digest(target),
                       'sources': table.metadata['provenance'], 'rows': len(table.rows),
                       'roundtrip': 'exact', 'source_modified': False}
            atomic_json(acceptance, receipt)
            result.update(status='accepted', rows=len(table.rows), bytes=target.stat().st_size)
    except Exception as error:  # noqa: BLE001 -- persist task failures; never mark them accepted
        result.update(error=f'{type(error).__name__}: {error}')
    result['seconds'] = time.monotonic()-started
    return result


def run(root, campaign, inventory_path, fnf_inventory, swe_inventory, *, workers=8,
        verify_resume=False, task_ids=None):
    if not 1 <= workers <= 8:
        raise ValueError('Workers must be 1–8')
    root, campaign = Path(root).resolve(), Path(campaign).resolve()
    if not campaign.is_relative_to(root/'observations/work/station-migration'):
        raise ValueError('Staging-only campaign required')
    campaign.mkdir(parents=True, exist_ok=True)
    with (campaign/'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = prepare(root, campaign, inventory_path, fnf_inventory, swe_inventory, task_ids=task_ids)
        campaign_sha = digest(campaign/'campaign.json')
        report = {'status': 'running', 'source_modified': False, 'workers': workers, 'passes': []}
        start = time.monotonic()
        for attempt in range(2 if verify_resume else 1):
            results = []
            report['passes'].append(results)
            args = [(str(root), str(campaign), e, manifest['metadata'], campaign_sha) for e in manifest['entries']]
            with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
                futures = {pool.submit(convert, arg): arg[2]['task_id'] for arg in args}
                for future in as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception as error:  # noqa: BLE001 -- includes process-pool worker failures
                        results.append({'task_id': futures[future], 'status': 'failed', 'error': str(error)})
                    atomic_json(campaign/'progress.json', report)
            if any(r['status'] == 'failed' for r in results):
                report['status'] = 'failed'
                break
            if attempt == 1 and any(r['status'] != 'skipped_verified' for r in results):
                report['status'] = 'failed'
                break
        if report['status'] != 'failed':
            paths = [campaign/'stations'/f"{e['task_id']}.nc" for e in manifest['entries']]
            try:
                write_catalog(campaign/'catalog.json', paths, lock_root=campaign/'locks')
                read_catalog(campaign/'catalog.json', verify=True)
                report['status'] = 'passed'
            except (OSError, ValueError, KeyError) as error:
                report.update(status='failed', catalog_error=str(error))
        report['seconds'] = time.monotonic()-start
        atomic_json(campaign/'acceptance.json', report)
        return report
