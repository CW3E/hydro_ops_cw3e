"""Permanent station collection activation and recoverable catalog transactions."""
import fcntl
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json

from .station_catalog import IDENTITY, describe_station, read_catalog
from .station_conversion import digest, inside
from .station_store import read_station, write_station
from .station_updates import merge_window

COLLECTION = 'observations/processed/stations/v1'
STATE = 'observations/status/stations'


@contextmanager
def operation_lock(root):
    state = Path(root)/STATE
    state.mkdir(parents=True, exist_ok=True)
    with (state/'operation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def active_catalog(root):
    path = Path(root)/STATE/'active.json'
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if record.get('status') != 'active' or record.get('catalog') != COLLECTION+'/catalog.json':
        raise ValueError('Station cutover is incomplete or invalid')
    return Path(root)/record['catalog']


def permanent_name(entry):
    parts = [str(entry[k]) for k in IDENTITY]
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in parts):
        raise ValueError('Unsafe station identity')
    return Path(*parts[:-1])/(parts[-1]+'.nc')


def copy_atomic(source, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=target.name+'.', suffix='.tmp', dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        if digest(temporary) != expected or digest(source) != expected:
            raise ValueError('Copy checksum mismatch')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def cutover(root, source_catalog, handoff):
    root = Path(root).resolve()
    source = inside(root, source_catalog)
    if not source.is_relative_to(root/'observations/work/station-migration'):
        raise ValueError('Accepted staged collection required')
    receipt = json.loads(source.with_suffix('.acceptance.json').read_text())
    test = json.loads(inside(root, handoff).read_text())
    sha = digest(source)
    if (receipt.get('status') != 'passed' or receipt.get('catalog_sha256') != sha
            or test.get('status') != 'passed' or test.get('source_catalog_sha256') != sha
            or not test.get('source_catalog_unchanged') or test.get('production_modified')):
        raise ValueError('Collection/handoff acceptance mismatch')
    with operation_lock(root):
        registry = root/STATE/'active.json'
        if registry.exists():
            current = json.loads(registry.read_text())
            if current.get('status') != 'preparing' or current.get('source_catalog_sha256') != sha:
                raise ValueError('Existing activation; review before retrying')
        # Block legacy and new writers while the permanent snapshot is copied.
        atomic_json(registry, {'status': 'preparing', 'source_catalog_sha256': sha,
                              'source_catalog': str(source.relative_to(root))})
        document = read_catalog(source)
        if len(document['entries']) != receipt['members']:
            raise ValueError('Collection count mismatch')
        destinations = [permanent_name(e) for e in document['entries']]
        if len(set(destinations)) != len(destinations):
            raise ValueError('Duplicate permanent paths')
        entries = []
        for entry, relative in zip(document['entries'], destinations, strict=True):
            origin = inside(source.parent, entry['path'])
            target = root/COLLECTION/relative
            if digest(origin) != entry['sha256']:
                raise ValueError('Staged member changed')
            if target.exists():
                if digest(target) != entry['sha256']:
                    raise ValueError('Conflicting permanent file; refusing overwrite')
            else:
                copy_atomic(origin, target, entry['sha256'])
            entries.append({**entry, 'path': str(relative)})
        catalog = root/COLLECTION/'catalog.json'
        atomic_json(catalog, {**document, 'entries': entries})
        read_catalog(catalog, verify=True)
        if digest(source) != sha:
            raise ValueError('Source catalog changed')
        record = {'status': 'active', 'catalog': str(catalog.relative_to(root)),
                  'initial_catalog_sha256': digest(catalog), 'members': len(entries),
                  'source_catalog': str(source.relative_to(root)), 'source_catalog_sha256': sha,
                  'handoff': str(inside(root, handoff).relative_to(root)),
                  'original_archives_retained': True, 'cron_installed': False}
        atomic_json(root/STATE/'cutover.json', record)
        # Single activation point: routing changes only after all copies verify.
        atomic_json(registry, record)
        return record


def recover_pending(root):
    """Caller holds operation_lock; recover only the recorded exact transaction."""
    root = Path(root).resolve()
    journal = root/STATE/'pending.json'
    if not journal.exists():
        return
    txn = json.loads(journal.read_text())
    if txn['status'] == 'complete':
        return
    catalog = active_catalog(root)
    if catalog is None:
        raise ValueError('No active collection')
    target = inside(catalog.parent, txn['entry']['path'])
    candidate = inside(root, txn['candidate'])
    if not candidate.is_relative_to(root/'observations/work/station-cycles'):
        raise ValueError('Invalid transaction candidate')
    if digest(candidate) != txn['entry']['sha256']:
        raise ValueError('Transaction candidate changed')
    doc = read_catalog(catalog)
    matches = [i for i, e in enumerate(doc['entries']) if e['path'] == txn['entry']['path']]
    if len(matches) != 1:
        raise ValueError('Transaction member missing or duplicated')
    index = matches[0]
    previous = doc['entries'][index]
    if (any(previous[k] != txn['entry'][k] for k in IDENTITY)
            or previous['sha256'] not in (txn['before'], txn['entry']['sha256'])):
        raise ValueError('Transaction catalog conflict')
    actual = digest(target)
    if actual not in (txn['before'], txn['entry']['sha256']):
        raise ValueError('Transaction station conflict')
    if actual != txn['entry']['sha256']:
        copy_atomic(candidate, target, txn['entry']['sha256'])
    doc['entries'][index] = txn['entry']
    atomic_json(catalog, doc)
    atomic_json(journal, {**txn, 'status': 'complete'})


def publish_window(root, entry, incoming, start, end, work):
    """Caller holds operation_lock; journal bridges station/catalog atomic renames."""
    root = Path(root).resolve()
    recover_pending(root)
    catalog = active_catalog(root)
    if catalog is None:
        raise ValueError('No active collection')
    target = inside(catalog.parent, entry['path'])
    if digest(target) != entry['sha256']:
        raise ValueError('Stale station selection')
    previous = read_station(target)
    merged = merge_window(previous, incoming, start=start, end=end)
    if merged == previous:
        return {'status': 'unchanged', 'entry': entry}
    work = Path(work).resolve()
    if not work.is_relative_to(root/'observations/work/station-cycles'):
        raise ValueError('Cycle workspace required')
    candidate = work/'candidate.nc'
    write_station(candidate, merged, lock_root=work/'locks')
    new = describe_station(candidate, work)
    new['path'] = entry['path']
    txn = {'status': 'pending', 'before': entry['sha256'], 'entry': new,
           'candidate': str(candidate.relative_to(root))}
    atomic_json(root/STATE/'pending.json', txn)
    recover_pending(root)
    return {'status': 'published', 'entry': new}
