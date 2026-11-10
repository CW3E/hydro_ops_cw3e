"""Durable, coalesced invalidations from downloaded PRISM release changes.

Discovery never marks a revision applied: consumers must acknowledge the exact
source signature after publishing hourly data, summaries, and domain subsets.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path


def source_signature(path: Path) -> dict:
    stat = path.stat()
    release = json.loads(path.with_suffix('.release.json').read_text())
    payload = {'path': str(path.resolve()), 'bytes': stat.st_size,
               'mtime_ns': stat.st_mtime_ns, 'inode': stat.st_ino, 'release': release}
    payload['signature'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def affected_days(prism_day: date) -> tuple[date, date]:
    return prism_day-timedelta(days=1), prism_day


def enqueue(state: dict, sources: dict, exists) -> dict:
    """Unknown provenance is pending, never silently assumed current."""
    state.setdefault('sources', {})
    state.setdefault('pending', {})
    for key, source in sources.items():
        if state['sources'].get(key, {}).get('signature') == source['signature']:
            continue
        _variable, stamp = key.split('/')
        for stream in ('retro', 'nrt'):
            for day in affected_days(date.fromisoformat(stamp)):
                if not exists(stream, day):
                    continue
                target = f'{stream}/{day}'
                event = state['pending'].setdefault(target, {'stream': stream, 'day': str(day), 'sources': {}})
                event['sources'][key] = source['signature']
                event['status'] = 'pending'
                event['reason'] = 'downloaded_prism_source_changed_or_untracked'
        state['sources'][key] = source
    return state


def acknowledge(state: dict, key: str, sources: dict, acceptance: dict) -> bool:
    """Do not clear newer revisions, incomplete downstream work or failed jobs."""
    if (key not in state.get('pending', {}) or state['pending'][key]['sources'] != sources
            or acceptance.get('status') != 'passed'
            or not all(acceptance.get(k) for k in ('hourly', 'summaries', 'subsets'))):
        return False
    del state['pending'][key]
    return True
