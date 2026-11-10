"""Versioned, lossless station tables used by the activated station collection.

Collection activation and catalog transactions live in station_operations;
this module validates and atomically replaces individual station histories.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

SCHEMA = 'hydro_ops_station_table_v1'


@dataclass
class StationTable:
    metadata: dict
    fields: list[str]
    rows: list[dict[str, str]]

    def normalized(self):
        mapping = self.metadata['mapping']
        return [{'series_id': r[mapping['series']], 'period_label': r[mapping['date']],
                 'value_si': float(r[mapping['value']]) if r[mapping['value']] else None,
                 'value_status': r[mapping['status']]} for r in self.rows]

    def validate(self):
        for key in ('provider', 'station_id', 'product', 'sensor', 'resolution', 'units_si',
                    'timezone', 'interval_semantics'):
            if not isinstance(self.metadata.get(key), str) or not self.metadata[key]:
                raise ValueError(f'Missing textual metadata: {key}')
        kind = self.metadata.get('label_kind', 'period')
        if kind not in ('period', 'fixed_pst_timestamp'):
            raise ValueError('Unknown label kind')
        if self.metadata['resolution'] not in ('daily', 'monthly', 'hourly', 'event'):
            raise ValueError('Unsupported resolution')
        if self.metadata['resolution'] in ('hourly', 'event') and kind != 'fixed_pst_timestamp':
            raise ValueError('Native timestamps required for hourly/event records')
        if not self.fields or len(set(self.fields)) != len(self.fields):
            raise ValueError('Unique source column names required')
        if set(self.metadata['mapping']) != {'series', 'date', 'value', 'status'}:
            raise ValueError('Explicit common-column mapping required')
        if not set(self.metadata['mapping'].values()) <= set(self.fields):
            raise ValueError('Mapped source columns absent')
        for row in self.rows:
            if set(row) != set(self.fields) or not all(isinstance(v, str) for v in row.values()):
                raise ValueError('Every source column must be preserved as text')
        keys = set()
        for row in self.normalized():
            label = row['period_label']
            if kind == 'fixed_pst_timestamp':
                stamp = datetime.fromisoformat(label)
                if stamp.utcoffset() != timedelta(hours=-8) or stamp.isoformat() != label:
                    raise ValueError('Canonical fixed-PST timestamp required')
            else:
                date.fromisoformat(label + '-01' if self.metadata['resolution'] == 'monthly' else label)
            key = row['series_id'], label
            if not key[0] or key in keys:
                raise ValueError('Empty series or duplicate series/period key')
            keys.add(key)
            if row['value_si'] is not None and not math.isfinite(row['value_si']):
                raise ValueError('Non-finite normalized value')
        return keys

    def signature(self):
        payload = json.dumps([self.metadata, self.fields, self.rows], sort_keys=True,
                             ensure_ascii=False, allow_nan=False).encode()
        return hashlib.sha256(payload).hexdigest()


def _put_text(group, name, values):
    """Compressed UTF-8 bytes plus lengths preserve whitespace and embedded NULs."""
    encoded = [v.encode('utf-8') for v in values]
    width = max(1, max(map(len, encoded), default=0))
    group.createDimension(name+'_width', width)
    data = np.zeros((len(values), width), dtype='u1')
    for i, value in enumerate(encoded):
        data[i, :len(value)] = np.frombuffer(value, dtype='u1')
    var = group.createVariable(name, 'u1', ('record', name+'_width'), zlib=True, complevel=4)
    var[:] = data
    var.encoding = 'UTF-8'
    group.createVariable(name+'_length', 'i4', ('record',), zlib=True, complevel=4)[:] = list(map(len, encoded))


def _get_text(group, name):
    data, lengths = group[name][:], group[name+'_length'][:]
    return [row[:int(n)].tobytes().decode('utf-8') for row, n in zip(data, lengths, strict=True)]


def read_station(path, *, start=None, end=None):
    """Read a whole table or inclusive provider-label interval, without UTC reinterpretation."""
    with Dataset(path) as ds:
        ds.set_auto_mask(False)
        if ds.schema != SCHEMA:
            raise ValueError('Unsupported station schema')
        meta = json.loads(_get_text(ds.groups['metadata'], 'json')[0])
        fields = json.loads(ds.source_fields)
        columns = [_get_text(ds.groups['source'], f'column_{i}') for i in range(len(fields))]
        table = StationTable(meta, fields, [dict(zip(fields, row, strict=True)) for row in zip(*columns, strict=True)])
        table.validate()
        if table.signature() != ds.content_sha256:
            raise ValueError('Station payload checksum mismatch')
        common = table.normalized()
        for key in ('series_id', 'period_label', 'value_status'):
            if _get_text(ds, key) != [r[key] for r in common]:
                raise ValueError('Normalized text differs from source')
        expected = np.array([np.nan if r['value_si'] is None else r['value_si'] for r in common])
        if not np.array_equal(ds['value_si'][:], expected, equal_nan=True):
            raise ValueError('Normalized numeric values differ from source')
    label = meta['mapping']['date']
    if start or end:
        table.rows = [r for r in table.rows if (start is None or r[label] >= start)
                      and (end is None or r[label] <= end)]
    return table


def write_station(path, table, *, lock_root, expected_signature=None):
    """Publish a full snapshot atomically; reject silent loss of existing observation keys."""
    keys = table.validate()
    path, lock_root = Path(path), Path(lock_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(str(path.resolve()).encode()).hexdigest()+'.lock'
    with (lock_root/lock_name).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if expected_signature is not None and not path.exists():
            raise ValueError('Station changed during refresh: file disappeared')
        if path.exists():
            previous = read_station(path)
            if expected_signature is not None and previous.signature() != expected_signature:
                raise ValueError('Station changed during refresh; re-read and retry')
            identity = ('provider', 'station_id', 'product', 'sensor', 'resolution', 'units_si',
                        'timezone', 'interval_semantics', 'mapping')
            if any(previous.metadata[k] != table.metadata[k] for k in identity):
                raise ValueError('Station/series semantics changed')
            if previous.metadata.get('label_kind', 'period') != table.metadata.get('label_kind', 'period'):
                raise ValueError('Station label semantics changed')
            if not previous.validate() <= keys:
                raise ValueError('History loss: existing series/period keys omitted')
            if not set(previous.fields) <= set(table.fields):
                raise ValueError('Source columns omitted from replacement')
            if previous.signature() == table.signature():
                return {'status': 'unchanged', 'rows': len(table.rows)}
        fd, tmp = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
        os.close(fd)
        try:
            with Dataset(tmp, 'w', format='NETCDF4') as ds:
                ds.schema = SCHEMA
                ds.content_sha256 = table.signature()
                ds.source_fields = json.dumps(table.fields)
                ds.createDimension('record', len(table.rows))
                meta = ds.createGroup('metadata')
                meta.createDimension('record', 1)
                _put_text(meta, 'json', [json.dumps(table.metadata, ensure_ascii=False, allow_nan=False)])
                source = ds.createGroup('source')
                for i, field in enumerate(table.fields):
                    _put_text(source, f'column_{i}', [r[field] for r in table.rows])
                common = table.normalized()
                for name in ('series_id', 'period_label', 'value_status'):
                    _put_text(ds, name, [r[name] for r in common])
                var = ds.createVariable('value_si', 'f8', ('record',), zlib=True, complevel=4,
                                        fill_value=np.nan)
                var.units = table.metadata['units_si']
                var[:] = [np.nan if r['value_si'] is None else r['value_si'] for r in common]
            if read_station(tmp) != table:
                raise ValueError('Station readback differs from input')
            with open(tmp, 'rb') as handle:
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(tmp).unlink(missing_ok=True)
    return {'status': 'published', 'rows': len(table.rows), 'content_sha256': table.signature()}
