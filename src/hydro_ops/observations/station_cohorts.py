"""Explicit USGS update scopes; never substitute a partial report for CONUS."""
import hashlib
import json
from pathlib import Path

SCOPES = ('all', 'regional', 'remainder')


def scope_name(selection, scope):
    if scope not in SCOPES or (selection != 'usgs' and scope != 'all'):
        raise ValueError('USGS scopes apply only to USGS updates')
    return selection if scope == 'all' else f'{selection}-{scope}'


def filter_entries(root, entries, scope):
    scope_name('usgs', scope)
    if scope == 'all':
        return entries, {'scope': 'all'}
    root = Path(root).resolve()
    raw = (root/'config/usgs_update_cohorts.json').read_bytes()
    config = json.loads(raw)
    if config['version'] != 1 or not config['inventories']:
        raise ValueError('Invalid cohort configuration')
    identifiers = set()
    for item in config['inventories']:
        p = (root/item['path']).resolve()
        if not p.is_relative_to(root):
            raise ValueError('Inventory outside project')
        content = p.read_bytes()
        if hashlib.sha256(content).hexdigest() != item['sha256']:
            raise ValueError('USGS domain inventory changed; review cohort configuration')
        identifiers.update(v if v.startswith('USGS-') else 'USGS-'+v for v in content.decode().split())
    if any(e['provider'] != 'usgs' for e in entries):
        raise ValueError('Expected USGS entries')
    selected = [e for e in entries if (e['station_id'] in identifiers) == (scope == 'regional')]
    if not selected:
        raise ValueError('Empty USGS update scope')
    return selected, {'scope': scope, 'config_sha256': hashlib.sha256(raw).hexdigest(),
                      'inventories': config['inventories'], 'count': len(selected)}
