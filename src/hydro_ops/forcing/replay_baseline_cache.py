"""Verified, isolated baseline imports for bounded PRISM replay."""
import shutil
import time
from pathlib import Path

from . import nrt_cycle
from .rain_repair_writer import digest
from .repair_transaction import atomic_json


def import_baseline(source, destination, expected, day):
    """Copy an accepted complete day, or return a cache miss without writes.

    Never link production inodes into a mutable replay workspace. Recheck the
    source receipt and identity after copying; all subsequent work is isolated.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('Baseline cache source and destination must differ')
    receipt_path = source.with_name(source.name + '.nrt-receipt.json')
    receipt = nrt_cycle.read_json(receipt_path)
    if (not nrt_cycle.up_to_date(source, receipt, expected)
            or receipt.get('complete_day') is not True
            or receipt.get('day') != str(day) or not receipt.get('sha256')):
        return None
    started = time.monotonic()
    if digest(source) != receipt['sha256']:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.cache-import')
    try:
        shutil.copy2(source, temporary)
        if (digest(temporary) != receipt['sha256']
                or nrt_cycle.read_json(receipt_path) != receipt
                or not nrt_cycle.up_to_date(source, receipt, expected)):
            raise ValueError('Baseline cache changed during import')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    imported = receipt | {
        'published_identity': nrt_cycle.identity(destination),
        'cache_import': {'source': str(source), 'seconds': time.monotonic() - started,
                         'source_identity': receipt['published_identity']},
    }
    atomic_json(destination.with_name(destination.name + '.nrt-receipt.json'), imported)
    return imported
