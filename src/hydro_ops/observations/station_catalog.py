"""Serialized catalog snapshots for station files; stale members fail closed."""
import fcntl
import hashlib
import json
import os
import tempfile
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

from .station_store import SCHEMA, read_station

CATALOG_SCHEMA = 'hydro_ops_station_catalog_v1'
IDENTITY = ('provider', 'station_id', 'product', 'sensor', 'resolution')


def _digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _member(base, name):
    p = (base/name).resolve()
    if Path(name).is_absolute() or not p.is_relative_to(base.resolve()):
        raise ValueError('Catalog member must remain inside collection root')
    return p


def describe_station(path, base):
    """Read and validate one station payload for a catalog entry."""
    p, base = Path(path).resolve(), Path(base).resolve()
    if not p.is_relative_to(base):
        raise ValueError('Station must be inside collection')
    digest = _digest(p)
    table = read_station(p)
    if _digest(p) != digest:
        raise ValueError('Station changed during catalog finalization')
    rows = table.normalized()
    labels = [r['period_label'] for r in rows]
    valid = [r['period_label'] for r in rows if r['value_si'] is not None]
    return {**{k: table.metadata[k] for k in IDENTITY},
            'path': str(p.relative_to(base)), 'bytes': p.stat().st_size,
            'sha256': digest, 'station_schema': SCHEMA,
            'content_sha256': table.signature(), 'rows': len(rows),
            'series_count': len({r['series_id'] for r in rows}), 'numeric_rows': len(valid),
            'first_period': min(labels, default=None), 'last_period': max(labels, default=None),
            'first_numeric_period': min(valid, default=None), 'last_numeric_period': max(valid, default=None),
            'value_status_counts': dict(Counter(r['value_status'] for r in rows)),
            'units_si': table.metadata['units_si'], 'timezone': table.metadata['timezone'],
            'interval_semantics': table.metadata['interval_semantics']}


def write_catalog(path, members, *, lock_root):
    """Finalize a collection after workers finish; never infer coverage from filenames."""
    path, lock_root = Path(path).resolve(), Path(lock_root)
    members = sorted(Path(p).resolve() for p in members)
    if len(members) != len(set(members)):
        raise ValueError('Duplicate catalog paths')
    if any(not p.is_relative_to(path.parent) or p == path for p in members):
        raise ValueError('Catalog members must be under the catalog directory')
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        # Same stable target locks as write_station, plus the catalog lock.
        for target in sorted([path, *members]):
            name = hashlib.sha256(str(target).encode()).hexdigest()+'.lock'
            lock = stack.enter_context((lock_root/name).open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        entries, identities = [], set()
        for p in members:
            digest = _digest(p)
            table = read_station(p)
            if _digest(p) != digest:
                raise ValueError('Station changed during catalog finalization')
            key = tuple(table.metadata[k] for k in IDENTITY)
            if key in identities:
                raise ValueError('Duplicate station/product/sensor/resolution identity')
            identities.add(key)
            rows = table.normalized()
            labels = [r['period_label'] for r in rows]
            valid = [r['period_label'] for r in rows if r['value_si'] is not None]
            entries.append({**{k: table.metadata[k] for k in IDENTITY},
                            'path': str(p.relative_to(path.parent)), 'bytes': p.stat().st_size,
                            'sha256': digest, 'station_schema': SCHEMA,
                            'content_sha256': table.signature(), 'rows': len(rows),
                            'series_count': len({r['series_id'] for r in rows}),
                            'numeric_rows': len(valid),
                            'first_period': min(labels, default=None),
                            'last_period': max(labels, default=None),
                            'first_numeric_period': min(valid, default=None),
                            'last_numeric_period': max(valid, default=None),
                            'value_status_counts': dict(Counter(r['value_status'] for r in rows)),
                            'units_si': table.metadata['units_si'],
                            'timezone': table.metadata['timezone'],
                            'interval_semantics': table.metadata['interval_semantics']})
        if path.exists():
            previous = read_catalog(path)
            old_keys = {tuple(e[k] for k in IDENTITY) for e in previous['entries']}
            if not old_keys <= identities:
                raise ValueError('Existing catalog members omitted; explicit retirement is required')
        document = {'schema': CATALOG_SCHEMA, 'status': 'validated_snapshot', 'entries': entries,
                    'scope': 'Explicit member list, not a provider-wide completeness claim'}
        payload = (json.dumps(document, sort_keys=True, indent=2)+'\n').encode()
        if path.exists() and path.read_bytes() == payload:
            return {'status': 'unchanged', 'members': len(entries)}
        fd, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {'status': 'published', 'members': len(entries)}


def read_catalog(path, *, verify=False):
    path = Path(path)
    document = json.loads(path.read_text())
    if document.get('schema') != CATALOG_SCHEMA:
        raise ValueError('Unsupported station catalog schema')
    keys, paths = set(), set()
    for entry in document['entries']:
        p = _member(path.parent, entry['path'])
        key = tuple(entry[k] for k in IDENTITY)
        if key in keys or p in paths:
            raise ValueError('Duplicate catalog identity or path')
        keys.add(key)
        paths.add(p)
        if verify and (not p.is_file() or _digest(p) != entry['sha256']):
            raise ValueError(f'Stale or missing catalog member: {p}')
    return document


def read_member(catalog, *, provider, station_id, product, sensor, resolution, start=None, end=None):
    document = read_catalog(catalog)
    key = (provider, station_id, product, sensor, resolution)
    entries = [e for e in document['entries'] if tuple(e[k] for k in IDENTITY) == key]
    if len(entries) != 1:
        raise KeyError(key)
    entry = entries[0]
    p = _member(Path(catalog).parent, entry['path'])
    if _digest(p) != entry['sha256']:
        raise ValueError('Catalog member changed; finalize catalog before reading')
    table = read_station(p, start=start, end=end)
    if _digest(p) != entry['sha256']:
        raise ValueError('Catalog member changed during read')
    return table
