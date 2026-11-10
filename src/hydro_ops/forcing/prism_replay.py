"""Plan exact-source PRISM revisions without confusing repaired fields with baselines."""
from datetime import date, timedelta
from pathlib import Path

from .prism_revision_queue import source_signature


def dependencies(day):
    """Two PRISM windows, three baseline days, and interval-end summary halos."""
    return {
        'windows': [str(day), str(day+timedelta(days=1))],
        'baselines': [str(day+timedelta(days=i)) for i in (-1, 0, 1)],
        'daily_summaries': [str(day-timedelta(days=1)), str(day)],
        'monthly_summaries': sorted({str(day-timedelta(days=1))[:7], str(day)[:7]}),
        'subset_order': ['cnrfc', 'b120', 'ucrb', 'yampa'],
    }


def plan(state, root):
    root = Path(root)
    rows = []
    for key, event in sorted(state.get('pending', {}).items()):
        day = date.fromisoformat(event['day'])
        if key != f'{event["stream"]}/{day}' or event['stream'] not in ('retro', 'nrt'):
            raise ValueError('Invalid pending revision key')
        item = {'key': key, 'stream': event['stream'], 'day': str(day),
                'queued_sources': dict(event['sources']), **dependencies(day)}
        item['missing_baselines'] = [d for d in item['baselines'] if not (
            root/'forcing/outputs/conus/baseline/hourly'/date.fromisoformat(d).strftime(
                '%Y/%m/%Y%m%d.LDASIN_DOMAIN1')).is_file()]
        item['source_snapshot'] = {}
        missing = []
        for window in item['windows']:
            for var in ('ppt', 'tmin', 'tmax'):
                d = date.fromisoformat(window)
                path = root/'forcing/inputs/oregon_state/prism/an/4km/daily'/var/d.strftime(
                    f'%Y/%m/prism_{var}_us_25m_%Y%m%d.nc')
                if not path.is_file() or not path.with_suffix('.release.json').is_file():
                    missing.append(f'{var}/{window}')
                else:
                    item['source_snapshot'][f'{var}/{window}'] = source_signature(path)
        stale = [k for k, v in event['sources'].items()
                 if item['source_snapshot'].get(k, {}).get('signature') != v]
        item.update(missing_prism=missing, superseded_queue_sources=stale,
                    status='blocked_sources' if missing or stale else
                    'needs_baseline' if item['missing_baselines'] else 'ready_for_candidate')
        rows.append(item)
    return {'status': 'planned', 'production_modified': False, 'automatic_replay_enabled': False,
            'acknowledgement_gate': 'exact frozen sources + accepted hourly + summaries + subsets',
            'items': rows}
