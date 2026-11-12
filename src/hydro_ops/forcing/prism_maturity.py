"""Explicit PRISM maturity selection, separate from source acquisition."""
import fcntl
import hashlib
import json
import shutil
import tempfile
import tomllib
from datetime import timedelta
from pathlib import Path

from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.repair_transaction import atomic_json

VARIABLES = ('ppt', 'tmin', 'tmax')


def implementation_fingerprint(root):
    names = ('config/prism_revision_policy.toml', 'src/hydro_ops/forcing/prism_maturity.py',
             'src/hydro_ops/forcing/prism_replay.py', 'src/hydro_ops/forcing/nrt_cycle.py',
             'src/hydro_ops/forcing/nrt_staged.py', 'bin/queue_prism_revisions.py',
             'bin/run_bounded_revision_queue.py', 'bin/update_nwm_forcing.py',
             'bin/submit_prism_calendar_batches.py', 'bin/produce_prism_constrained_daily.py',
             'bin/test_prism_maturity_policy.py', 'bin/test_isolated_revision_replay.py')
    return {n: hashlib.sha256((Path(root)/n).read_bytes()).hexdigest() for n in names}


def policy(root):
    path = Path(root)/'config/prism_revision_policy.toml'
    config = tomllib.loads(path.read_text()) if path.exists() else {'enabled': False}
    if config.get('enabled') and config.get('activation_report'):
        gate = Path(root)/config['activation_report']
        accepted = json.loads(gate.read_text()) if gate.exists() else {}
        config = {**config, 'enabled': accepted.get('status') == 'passed'
                  and accepted.get('policy') == 'two_maturity_stages_v1'}
    if config.get('enabled') and (config.get('nrt_grid_count') != 2
            or config.get('retro_minimum_grid_count') != 8 or config.get('nrt_window_days') != 11):
        raise ValueError('Unvalidated PRISM maturity policy')
    return config


def input_root(root, stream, *, enabled=None):
    enabled = policy(root).get('enabled', False) if enabled is None else enabled
    suffix = 'selected/nrt/daily' if enabled and stream == 'nrt' else 'an/4km/daily'
    return Path(root)/'forcing/inputs/oregon_state/prism'/suffix


def paths(root, day):
    return [Path(root)/v/day.strftime(f'%Y/%m/prism_{v}_us_25m_%Y%m%d.nc') for v in VARIABLES]


def release_ready(path, stream):
    try:
        count = int(json.loads(Path(path).with_suffix('.release.json').read_text())['grid_count'])
        return Path(path).is_file() and (count == 2 if stream == 'nrt' else count >= 8)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def day_ready(root, day, stream):
    if (stream == 'nrt' and policy(root).get('enabled')
            and not all((input_root(root, 'nrt')/'selections'/f'{d:%Y%m%d}.json').is_file()
                        for d in (day, day+timedelta(days=1)))):
        return False
    return all(release_ready(p, stream) for d in (day, day+timedelta(days=1))
               for p in paths(input_root(root, stream), d))


def selected_nrt_paths(root, day):
    base = input_root(root, 'nrt')
    result = []
    for d in (day, day+timedelta(days=1)):
        marker = base/'selections'/f'{d:%Y%m%d}.json'
        ready = not policy(root).get('enabled') or marker.is_file()
        if policy(root).get('enabled') and ready:
            validate_selection(marker)
        result += [p if ready else p.with_suffix('.awaiting-selection') for p in paths(base, d)]
    return result


def validate_selection(marker):
    record = json.loads(marker.read_text())
    if record['status'] != 'selected' or record['grid_count'] != 2 or len(record['sources']) != 3:
        raise ValueError('Invalid selected PRISM manifest')
    for row in record['sources']:
        if source_signature(Path(row['selected']['path'])) != row['selected']:
            raise ValueError('Selected PRISM release changed')
    return record


def pin_nrt_day(root, day, today):
    """Caller holds revision queue lock; freeze exactly release 2, never substitute 3+."""
    destination = input_root(root, 'nrt', enabled=True)
    marker = destination/'selections'/f'{day:%Y%m%d}.json'
    if marker.exists():
        return validate_selection(marker)
    if not 0 < (today-day).days <= 11:
        return {'status': 'outside_nrt_window', 'day': str(day)}
    original = paths(input_root(root, 'retro', enabled=True), day)
    if not all(release_ready(p, 'nrt') for p in original):
        counts = []
        for p in original:
            try:
                counts.append(int(json.loads(p.with_suffix('.release.json').read_text())['grid_count']))
            except (OSError, ValueError, KeyError, TypeError):
                counts.append(0)
        if any(c > 2 for c in counts):
            return {'status': 'second_release_missed', 'day': str(day), 'available_counts': counts}
        return {'status': 'awaiting_second_release', 'day': str(day)}
    selected = paths(destination, day)
    rows = []
    for source, target in zip(original, selected, strict=True):
        with source.with_suffix('.download.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            before = source_signature(source)
            if not release_ready(source, 'nrt'):
                raise ValueError('Second release changed before selection lock')
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.select-', dir=target.parent) as temp:
                copy = Path(temp)/target.name
                shutil.copy2(source, copy)
                sha = hashlib.sha256(copy.read_bytes()).hexdigest()
                if source_signature(source) != before or hashlib.sha256(source.read_bytes()).hexdigest() != sha:
                    raise ValueError('PRISM changed while selecting release')
                copy.replace(target)
            atomic_json(target.with_suffix('.release.json'), before['release'])
            rows.append({'original': before, 'selected': source_signature(target), 'sha256': sha})
    record = {'status': 'selected', 'day': str(day), 'grid_count': 2, 'sources': rows}
    atomic_json(marker, record)
    return record


def apply_queue_policy(state, root, today, sources):
    """Retire out-of-policy obligations with evidence; never call them applied."""
    from hydro_ops.forcing.prism_revision_queue import enqueue
    before = dict(state.get('pending', {}))
    dispositions = state.setdefault('policy_dispositions', {})
    pending = {}
    selections = {}
    for stream in ('nrt', 'retro'):
        chosen = {}
        for key, source in sources.items():
            if stream == 'nrt':
                from datetime import date
                day = date.fromisoformat(key.split('/')[1])
                if not 0 < (today-day).days <= 11:
                    continue
                if day not in selections:
                    selections[day] = pin_nrt_day(root, day, today)
                record = selections[day]
                state.setdefault('nrt_selection_status', {})[str(day)] = record['status']
                if record['status'] != 'selected':
                    continue
                var = key.split('/')[0]
                chosen[key] = next(r['selected'] for r in record['sources']
                                   if Path(r['selected']['path']).name.startswith(f'prism_{var}_'))
            elif release_ready(Path(source['path']), 'retro'):
                chosen[key] = source
        # Preserve previously acknowledged stable identities during migration;
        # do not turn a policy change into a replay of all known stable history.
        histories = state.setdefault('maturity_sources', {}).setdefault(
            stream, dict(state.get('sources', {})) if stream == 'retro' else {})
        existing = {k: e for k, e in before.items() if e['stream'] == stream}
        def eligible(s, d, stream=stream):
            return (s == stream and (stream != 'nrt' or 0 < (today-d).days <= 11)
                    and (Path(root)/f'forcing/outputs/conus/{s}/hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')).is_file())
        valid = {}
        for k, e in existing.items():
            from datetime import date
            if eligible(stream, date.fromisoformat(e['day'])) and all(
                    chosen.get(v, {}).get('signature') == sig for v, sig in e['sources'].items()):
                valid[k] = e
            else:
                dispositions[k] = {'status': 'not_applied_policy', 'policy': 'two_maturity_stages_v1',
                                   'reviewed_at': str(today), 'event': e,
                                   'reason': 'outside_window_or_unselected_release'}
        sub = enqueue({'sources': histories, 'pending': valid}, chosen, eligible)
        pending.update(sub['pending'])
        state['maturity_sources'][stream] = sub['sources']
    state['pending'] = pending
    state['sources'] = sources
    state['revision_policy'] = 'two_maturity_stages_v1'
    return state
