"""Crash-recoverable rain publication and durable metadata helpers.

Publication defaults to forcing/work sandboxes. The explicit production mode
requires an audited plan and exact CONUS Retro calendar destination; it is not
a general-purpose file replacement API.
"""
import fcntl
import json
import os
import shutil
import tempfile
from datetime import UTC
from pathlib import Path

from hydro_ops.forcing.rain_repair_writer import digest, fingerprint


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, suffix='.part', delete=False) as f:
        temporary = Path(f.name)
        json.dump(value, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sandbox(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(Path(root).resolve() / 'forcing/work'):
        raise ValueError('Publication prototype is restricted to forcing/work')
    return path


def create_snapshot(source, destination, dates, root, *, source_overrides=None):
    """Metadata-only hard links: preserve old inodes across atomic replacements."""
    source, destination = Path(source).resolve(), sandbox(destination, root)
    destination.mkdir(parents=True, exist_ok=False)
    rows = []
    for day in dates:
        relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        original = Path((source_overrides or {}).get(day, source / relative))
        target = destination / relative
        before = fingerprint(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(original, target)
        if fingerprint(original) != before or fingerprint(target) != before:
            raise ValueError(f'Snapshot input changed: {original}')
        rows.append({'relative': relative, 'identity': list(before), 'source': str(original)})
    manifest = {'status': 'passed', 'source_root': str(source), 'snapshot_root': str(destination),
                'files': rows, 'policy': 'hard links; atomic replacement only; no in-place updates'}
    atomic_json(destination / 'snapshot.json', manifest)
    return manifest


def check_snapshot(manifest_path, original_root):
    record = json.loads(Path(manifest_path).read_text())
    root = Path(original_root).resolve()
    if (root/'RETIRED.json').exists():
        raise ValueError('Original snapshot retired after accepted publication; rebuild inputs for a new campaign')
    if record['status'] != 'passed' or Path(record['snapshot_root']).resolve() != root:
        raise ValueError('Snapshot root/status mismatch')
    for row in record['files']:
        path = (root / row['relative']).resolve()
        if not path.is_relative_to(root) or list(fingerprint(path)) != row['identity']:
            raise ValueError(f'Snapshot changed: {path}')
    return record


def publish(plan_path, root, *, interrupt_after_replace=False, production=False):
    """Idempotent sandbox replacement with intent journal and immutable old copy.

    Receipt and data are NOT a multi-file atomic transaction. Consumers must use
    the receipt state; interrupted intent is reconciled before marking ready.
    """
    plan_path = sandbox(plan_path, root)
    plan = json.loads(plan_path.read_text())
    plan_hash = digest(plan_path)
    if production:
        from datetime import datetime
        target = Path(plan['target']).resolve()
        day = datetime.strptime(target.name, '%Y%m%d.LDASIN_DOMAIN1').replace(tzinfo=UTC)
        expected = Path(root).resolve() / 'forcing/outputs/conus/retro/hourly' / day.strftime(
            '%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        if target != expected or plan.get('authorization') != 'audited-rain-only-production-v1':
            raise ValueError('Production publication requires exact calendar destination and authorization')
    else:
        target = sandbox(plan['target'], root)
    candidate = sandbox(plan['candidate'], root)
    if any((parent/'RETIRED.json').exists() for parent in candidate.parents
           if parent.is_relative_to(Path(root).resolve()/'forcing/work/prism-rain-pipeline')):
        raise ValueError('Pipeline candidate retired; historical publication retry is disabled')
    backup = sandbox(plan['original_snapshot'], root)
    if any((parent/'RETIRED.json').exists() for parent in backup.parents
           if parent.is_relative_to(Path(root).resolve()/'forcing/work')):
        raise ValueError('Original snapshot retired; historical publication retry is disabled')
    receipt = sandbox(plan['receipt'], root)
    audit_path = sandbox(plan['audit'], root)
    lock_path = target.with_name(target.name + '.repair.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        audit = json.loads(audit_path.read_text())
        matches = [r for r in audit.get('files', []) if Path(r['path']).resolve() == candidate]
        if (audit.get('status') != 'passed' or len(matches) != 1
                or matches[0]['sha256'] != plan['new_sha256']):
            raise ValueError('Candidate lacks matching passed audit')
        if digest(audit_path) != plan['audit_sha256']:
            raise ValueError('Audit evidence changed')
        if digest(candidate) != plan['new_sha256'] or digest(backup) != plan['old_sha256']:
            raise ValueError('Candidate or original snapshot changed')
        state = json.loads(receipt.read_text()) if receipt.exists() else None
        if state is not None and state.get('plan_sha256') != plan_hash:
            raise ValueError('Receipt belongs to another plan')
        current = digest(target)
        if state and state['status'] == 'published':
            if current != plan['new_sha256']:
                raise ValueError('Published destination changed')
            return {'status': 'already_published', 'path': str(target)}
        if current == plan['new_sha256']:
            if not state or state['status'] != 'intent':
                raise ValueError('Unexpected replacement without recorded intent')
            recovered = True
        else:
            if current != plan['old_sha256'] or list(fingerprint(target)) != plan['old_identity']:
                raise ValueError('Destination changed since planning')
            atomic_json(receipt, {'status': 'intent', 'plan_sha256': plan_hash,
                                  'old_sha256': plan['old_sha256'], 'new_sha256': plan['new_sha256']})
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.part', delete=False) as f:
                staging = Path(f.name)
            try:
                shutil.copyfile(candidate, staging)
                if digest(staging) != plan['new_sha256']:
                    raise ValueError('Staging checksum mismatch')
                if list(fingerprint(target)) != plan['old_identity']:
                    raise ValueError('Destination changed before replacement')
                with staging.open('rb') as f:
                    os.fsync(f.fileno())
                os.replace(staging, target)
                sync_directory(target.parent)
            finally:
                staging.unlink(missing_ok=True)
            if interrupt_after_replace:
                raise RuntimeError('Injected interruption after replacement')
            recovered = False
        atomic_json(receipt, {'status': 'published', 'plan_sha256': plan_hash,
                              'sha256': plan['new_sha256'], 'identity': list(fingerprint(target)),
                              'recovered_after_replace': recovered, 'derived_products_pending': True,
                              'original_snapshot': str(backup), 'audit': str(audit_path)})
        return {'status': 'published', 'recovered_after_replace': recovered, 'path': str(target)}
