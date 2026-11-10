"""Receipt-gated atomic summary replacement; caller holds publication locks."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .rain_repair_writer import digest, fingerprint
from .repair_transaction import atomic_json


def check_inputs(inputs):
    if any(list(fingerprint(Path(p))) != value for p, value in inputs.items()):
        raise ValueError('Summary source identity changed; replan required')


def publish_summary(candidate, target, receipt, inputs, validation, *, after_replace=None):
    """Resume a prepared replacement or skip a verified committed replacement.

    An old hardlink is retained until explicit audited cleanup. File replacement
    and receipt publication are deliberately separate, with recoverable intent.
    """
    candidate, target, receipt = map(Path, (candidate, target, receipt))
    if candidate.resolve() == target.resolve():
        raise ValueError('Candidate must be isolated')
    if validation.get('status') != 'passed':
        raise ValueError('Candidate validation has not passed')
    check_inputs(inputs)
    checksum = digest(candidate)
    if receipt.exists():
        record = json.loads(receipt.read_text())
        if (record['target'] != str(target.resolve()) or record['sha256'] != checksum
                or record['inputs'] != inputs or record['validation'] != validation):
            raise ValueError('Publication intent differs; inspect before retry')
        if target.exists() and digest(target) == checksum:
            check_inputs(inputs)
            recovered = record['status'] != 'published'
            record.update(status='published', identity=list(fingerprint(target)),
                          recovered_after_replace=recovered or record.get('recovered_after_replace', False))
            atomic_json(receipt, record)
            return {'status': 'recovered' if recovered else 'unchanged', 'target': str(target)}
        if record['status'] == 'published':
            raise ValueError('Published summary was changed externally')
        if (list(fingerprint(target)) if target.exists() else None) != record['old_identity']:
            raise ValueError('Original summary changed; refusing replacement')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        old_identity = list(fingerprint(target)) if target.exists() else None
        backup = receipt.with_name(receipt.name + '.original')
        if old_identity is not None:
            if not backup.exists():
                os.link(target, backup)
            if list(fingerprint(backup)) != old_identity:
                raise ValueError('Original backup identity mismatch')
        record = {'status': 'prepared', 'target': str(target.resolve()),
                  'candidate': str(candidate.resolve()), 'sha256': checksum,
                  'old_identity': old_identity, 'backup': str(backup) if old_identity else None,
                  'inputs': inputs, 'validation': validation}
        atomic_json(receipt, record)
    temporary = target.with_name(target.name + '.rain-summary.partial')
    # Existing partials are this locked transaction's incomplete transfer only.
    with candidate.open('rb') as source, temporary.open('wb') as destination:
        shutil.copyfileobj(source, destination, length=8*1024*1024)
        destination.flush()
        os.fsync(destination.fileno())
    if digest(temporary) != checksum:
        raise ValueError('Candidate transfer checksum mismatch')
    check_inputs(inputs)
    if (list(fingerprint(target)) if target.exists() else None) != record['old_identity']:
        raise ValueError('Original summary changed before replacement')
    temporary.replace(target)
    if after_replace:
        after_replace()
    record.update(status='published', identity=list(fingerprint(target)),
                  recovered_after_replace=False)
    atomic_json(receipt, record)
    return {'status': 'published', 'target': str(target)}
