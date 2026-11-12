"""Isolated station preparation; only the serialized coordinator publishes.

Experimental until the replay benchmark and live acquisition tests pass.
NetCDF preparation belongs in separate processes, never concurrent threads.
"""
import time
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json

from . import station_operations as ops
from .station_catalog import IDENTITY, describe_station, read_catalog
from .station_conversion import digest, inside
from .station_store import read_station, write_station
from .station_updates import merge_window, retain_omitted


def prepare_window(root, entry, incoming_path, start, end, work):
    """Read history once and prepare a fully readback-verified candidate."""
    root, work = Path(root).resolve(), Path(work).resolve()
    if not work.is_relative_to(root/'observations/work/station-cycles'):
        raise ValueError('Station-cycle preparation workspace required')
    work.mkdir(parents=True, exist_ok=False)
    timings = {}

    def phase(name):
        atomic_json(work/'progress.json', {'status': 'running', 'phase': name,
                                         'path': entry['path'], 'timings': timings})
        return time.monotonic()

    try:
        mark = phase('read_and_verify')
        catalog = ops.active_catalog(root)
        if catalog is None:
            raise ValueError('Station collection not active')
        source = inside(catalog.parent, entry['path'])
        if digest(source) != entry['sha256']:
            raise ValueError('Stale station selection')
        previous = read_station(source)
        incoming = read_station(incoming_path)
        timings['read_seconds'] = time.monotonic()-mark
        mark = phase('merge')
        incoming, omitted = retain_omitted(previous, incoming, start=start, end=end)
        merged = merge_window(previous, incoming, start=start, end=end)
        timings['merge_seconds'] = time.monotonic()-mark
        result = {'status': 'prepared', 'before': entry, 'entry': entry,
                  'changed': merged != previous, 'omitted_keys_retained': omitted,
                  'timings': timings, 'start': start, 'end': end}
        if result['changed']:
            mark = phase('write_and_readback')
            candidate = work/'candidate.nc'
            write_station(candidate, merged, lock_root=work/'locks')
            timings['write_seconds'] = time.monotonic()-mark
            mark = phase('describe_candidate')
            new = describe_station(candidate, work)
            new['path'] = entry['path']
            result.update(entry=new, candidate=str(candidate))
            timings['describe_seconds'] = time.monotonic()-mark
        if digest(source) != entry['sha256']:
            raise ValueError('Source changed during preparation')
        atomic_json(work/'prepared.json', result)
        atomic_json(work/'progress.json', result)
        return result
    except Exception as error:
        atomic_json(work/'progress.json', {'status': 'failed', 'error': str(error),
                                         'path': entry['path'], 'timings': timings})
        raise


def identity(path):
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


class PreparedPublisher:
    """Caller holds operation_lock for this object's entire lifetime.

    Validate catalog paths once. Keep its indexed document in memory, but still
    atomically publish the complete catalog and journal for every station.
    Interrupted transactions remain recoverable by the existing recover_pending.
    """

    def __init__(self, root):
        self.root = Path(root).resolve()
        ops.recover_pending(self.root)
        self.catalog = ops.active_catalog(self.root)
        if self.catalog is None:
            raise ValueError('Station collection not active')
        self.catalog_identity = identity(self.catalog)
        self.document = read_catalog(self.catalog)
        if identity(self.catalog) != self.catalog_identity:
            raise ValueError('Catalog changed during initialization')
        self.index = {e['path']: i for i, e in enumerate(self.document['entries'])}
        self.failed = False

    def publish(self, prepared):
        if self.failed:
            raise ValueError('Publisher failed; recover before further publication')
        try:
            return self._publish(prepared)
        except Exception:
            self.failed = True
            raise

    def _publish(self, prepared):
        if identity(self.catalog) != self.catalog_identity:
            raise ValueError('Catalog changed outside coordinator')
        if prepared.get('status') != 'prepared':
            raise ValueError('Preparation not accepted')
        before, entry = prepared['before'], prepared['entry']
        if any(before[k] != entry[k] for k in (*IDENTITY, 'path')):
            raise ValueError('Prepared station identity changed')
        index = self.index.get(before['path'])
        target = inside(self.catalog.parent, before['path'])
        if (index is None or self.document['entries'][index] != before
                or digest(target) != before['sha256']):
            raise ValueError('Prepared source is stale')
        if not prepared['changed']:
            if entry != before:
                raise ValueError('Unchanged preparation modified catalog entry')
            return {'status': 'unchanged', 'entry': entry}
        candidate = inside(self.root, prepared['candidate'])
        if (not candidate.is_relative_to(self.root/'observations/work/station-cycles')
                or digest(candidate) != entry['sha256']):
            raise ValueError('Invalid prepared candidate')
        journal = self.root/ops.STATE/'pending.json'
        transaction = {'status': 'pending', 'before': before['sha256'], 'entry': entry,
                       'candidate': str(candidate.relative_to(self.root))}
        atomic_json(journal, transaction)
        ops.copy_atomic(candidate, target, entry['sha256'])
        self.document['entries'][index] = entry
        atomic_json(self.catalog, self.document)
        atomic_json(journal, transaction | {'status': 'complete'})
        self.catalog_identity = identity(self.catalog)
        return {'status': 'published', 'entry': entry}


def publish_prepared(root, prepared):
    """Single-update convenience; caller holds operation_lock."""
    return PreparedPublisher(root).publish(prepared)
