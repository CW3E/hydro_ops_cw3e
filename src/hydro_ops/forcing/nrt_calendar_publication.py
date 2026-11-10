"""Audit and publish legacy calendar PRISM candidates with current NRT receipts."""
import fcntl
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from netCDF4 import Dataset

from .gfs_publication import _atomic_json
from .nrt_cycle import (
    POLICY,
    RecentNrt,
    configuration,
    day_path,
    fingerprint,
    identity,
    read_json,
    transfer,
)


def publish(root, source, destination, baseline, day, work, state):
    root, source, destination = Path(root), Path(source), Path(destination)
    state, work = Path(state), Path(work)
    state.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    lock_path = root/'forcing/status/nrt-gfs/cycle.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _publish(root, source, destination, Path(baseline), day, work, state)


def _publish(root, source, destination, baseline, day, work, state):
    manifest_path = source.with_name(source.name+'.manifest.json')
    manifest = read_json(manifest_path)
    if manifest.get('verified') is not True:
        raise ValueError('Calendar candidate lacks verified assembly manifest')
    base = day_path(baseline, day)
    base_receipt = read_json(base.with_name(base.name+'.nrt-receipt.json'))
    if (base_receipt.get('status') != 'passed' or base_receipt.get('published_identity') != identity(base)
            or len(base_receipt.get('hourly_primary_sources', [])) != 24):
        raise ValueError('Current complete source-aware baseline receipt required')
    original_identity = identity(source)
    baseline_identity = identity(base)
    envelope = root/configuration(root)['envelope']
    envelope_identity = identity(envelope)
    with Dataset(envelope) as data:
        repair = SimpleNamespace(active=np.asarray(data['active'][:], bool), keep=np.asarray(data['keep'][:], bool))
    inputs = {'publication_route': 'calendar_prism_nrt_finalizer_v1',
              'calendar_assembly': manifest, 'baseline': baseline_identity,
              'envelope': envelope_identity}
    backup = state/day.isoformat()
    backup.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not (backup/'original.LDASIN_DOMAIN1').exists():
        os.link(destination, backup/'original.LDASIN_DOMAIN1')
        for suffix in ('.manifest.json', '.nrt-receipt.json'):
            sidecar = destination.with_name(destination.name+suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, backup/('original'+suffix))
    with tempfile.TemporaryDirectory(prefix='nrt-calendar-final-', dir=work) as folder:
        candidate = Path(folder)/source.name
        shutil.copyfile(source, candidate)
        writes = RecentNrt.audit_final(SimpleNamespace(repair=repair), candidate, day, True)
        if identity(source) != original_identity or identity(base) != baseline_identity or identity(envelope) != envelope_identity:
            raise ValueError('Publication inputs changed during final audit')
        checksum = transfer(candidate, destination)
    receipt = {'status': 'passed', 'day': str(day), 'complete_day': True,
               'latest_model_ready_hour': f'{day}T23:00:00+00:00',
               'input_fingerprint': fingerprint(inputs), 'inputs': inputs,
               'published_identity': identity(destination), 'sha256': checksum,
               'as_of': datetime.now(UTC).isoformat(), 'prism_constrained': True,
               'gfs_hours': base_receipt['gfs_hours'], 'hourly_primary_sources': base_receipt['hourly_primary_sources'],
               'publication_route': 'calendar_prism_nrt_finalizer_v1', 'audit_record_writes': writes}
    receipt_path = destination.with_name(destination.name+'.nrt-receipt.json')
    _atomic_json(receipt_path, receipt)
    _atomic_json(destination.with_name(destination.name+'.manifest.json'), {
        'daily_file': str(destination), 'verified': True, 'forcing_stream': 'nrt',
        'verification': POLICY, 'nrt_receipt': str(receipt_path), **receipt})
    _atomic_json(backup/'acceptance.json', receipt)
    return receipt
