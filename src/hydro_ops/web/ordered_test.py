"""Two-generation commissioning, restricted to a unique remote test directory."""
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

from .globus_transfer import TEST_PREFIX, atomic_json, child_path, digest, lock, source_path


def prepare(project, config):
    project = Path(project).resolve()
    run_id = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
    folder = project / 'web/work/globus-ordered-tests' / run_id
    folder.mkdir(parents=True)
    generations = []
    for generation in (1, 2):
        local = folder / str(generation)
        local.mkdir()
        values = {
            'record.json': ('payload', {'generation': generation, 'value': generation * 10}),
            'unchanged.json': ('payload', {'constant': True}),
            ('obsolete.json' if generation == 1 else 'new.json'): ('payload', {'generation': generation}),
            'metadata.json': ('metadata', {'generation': generation, 'record': 'record.json'}),
            'catalog.json': ('catalog', {'generation': generation, 'metadata': 'metadata.json'}),
        }
        rows = []
        for name, (role, value) in values.items():
            path = local / name
            atomic_json(path, value)
            rows.append({'name': name, 'role': role, 'local': str(path.relative_to(project)),
                         'sha256': digest(path)})
        generations.append(rows)
    plan = {'schema': 'hydro_ops_ordered_test_v1', 'id': run_id,
            'config': config, 'generations': generations}
    atomic_json(folder / 'plan.json', plan)
    atomic_json(folder / 'state.json', {'plan_sha256': digest(folder / 'plan.json'), 'completed': []})
    return folder / 'plan.json'


def validate(project, path):
    path = Path(path)
    plan = json.loads(path.read_text())
    state = json.loads(path.with_name('state.json').read_text())
    if state['plan_sha256'] != digest(path) or plan['schema'] != 'hydro_ops_ordered_test_v1':
        raise ValueError('Frozen plan changed')
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', plan['id']):
        raise ValueError('Invalid isolated test ID')
    for rows in plan['generations']:
        for row in rows:
            if row['name'] not in ('record.json', 'unchanged.json', 'obsolete.json', 'new.json',
                                   'metadata.json', 'catalog.json'):
                raise ValueError('Unexpected test filename')
            local = Path(project) / row['local']
            source_path(project, local, plan['config']['source_project_root'])
            if digest(local) != row['sha256']:
                raise ValueError('Frozen payload changed')
    return plan, state


def remote(plan, name):
    relative = f'{TEST_PREFIX}/{plan["id"]}/{name}'
    return child_path(plan['config']['destination_root'], relative)


def check_http(plan, rows, get):
    for row in rows:
        relative = f'{TEST_PREFIX}/{plan["id"]}/{row["name"]}'
        response = get(urljoin(plan['config']['public_base_url'], relative),
                       timeout=30, allow_redirects=False, headers={'Cache-Control': 'no-cache'})
        if response.status_code != 200 or hashlib.sha256(response.content).hexdigest() != row['sha256']:
            raise ValueError('HTTPS bytes do not match generation')


def advance(project, path, client, *, retry_failed=False, get=requests.get):
    """One nonblocking step; durable submission IDs make interruption safe."""
    import globus_sdk

    path = Path(path)
    with lock(path.with_suffix('.lock')):
        plan, state = validate(project, path)
        phases = [(g, role) for g in (1, 2) for role in ('payload', 'metadata', 'catalog')]
        phases.append((2, 'cleanup'))
        index = len(state['completed'])
        if index == len(phases):
            return state
        generation, role = phases[index]
        config = plan['config']
        rows = [r for r in plan['generations'][generation - 1] if r['role'] == role]
        # Exact old-minus-new inventory, never a recursive directory delete.
        obsolete = sorted({r['name'] for r in plan['generations'][0]} -
                          {r['name'] for r in plan['generations'][1]})
        current = state.setdefault('current', {'generation': generation, 'role': role})

        def save():
            atomic_json(path.with_name('state.json'), state)

        if current.get('task_id'):
            current['status'] = client.get_task(current['task_id'])['status']
            save()
            if current['status'] == 'FAILED':
                state['phase'] = 'blocked'
                if retry_failed:
                    state.setdefault('failed_attempts', []).append(dict(current))
                    state['current'] = {'generation': generation, 'role': role}
                    state['phase'] = 'ready'
                save()
                return state
            if current['status'] != 'SUCCEEDED':
                return state
            if role == 'cleanup':
                check_http(plan, plan['generations'][1], get)
                for name in obsolete:
                    url = urljoin(config['public_base_url'], f'{TEST_PREFIX}/{plan["id"]}/{name}')
                    response = get(url, timeout=30, allow_redirects=False,
                                   headers={'Cache-Control': 'no-cache'})
                    if response.status_code not in (404, 410):
                        raise ValueError('Obsolete file still publicly available')
            else:
                check_http(plan, rows, get)
                if generation == 2 and role != 'catalog':
                    # Metadata/catalog must remain the preceding generation until
                    # their own gated phase runs, despite payload replacement.
                    old_roles = ('metadata', 'catalog') if role == 'payload' else ('catalog',)
                    check_http(plan, [r for r in plan['generations'][0]
                                      if r['role'] in old_roles], get)
                if role == 'catalog':
                    check_http(plan, plan['generations'][generation - 1], get)
            current['verified_at'] = datetime.now(UTC).isoformat()
            state['completed'].append(current)
            del state['current']
            state['phase'] = 'passed' if len(state['completed']) == len(phases) else 'ready'
            save()
            return state
        if role == 'cleanup':
            check_http(plan, plan['generations'][1], get)
            atomic_json(path.with_name('deletion-manifest.json'), {
                'test_id': plan['id'], 'recursive': False,
                'destination_collection_id': config['destination_collection_id'],
                'paths': [remote(plan, name) for name in obsolete]})
        if not current.get('submission_id'):
            current['submission_id'] = client.get_submission_id()['value']
            save()
        label = f'hydro-ops ordered test {plan["id"]} g{generation} {role}'
        if role == 'cleanup':
            data = globus_sdk.DeleteData(config['destination_collection_id'], label=label,
                notify_on_succeeded=False, notify_on_failed=True, notify_on_inactive=False,
                submission_id=current['submission_id'], recursive=False, interpret_globs=False,
                ignore_missing=True)
            for name in obsolete:
                data.add_item(remote(plan, name))
            result = client.submit_delete(data)
        else:
            data = globus_sdk.TransferData(source_endpoint=config['source_collection_id'],
                notify_on_succeeded=False, notify_on_failed=True, notify_on_inactive=False,
                destination_endpoint=config['destination_collection_id'], label=label,
                submission_id=current['submission_id'], sync_level='checksum', verify_checksum=True,
                delete_destination_extra=False)
            for row in rows:
                data.add_item(source_path(project, Path(project) / row['local'], config['source_project_root']),
                    remote(plan, row['name']), recursive=False,
                    external_checksum=row['sha256'], checksum_algorithm='sha256')
            result = client.submit_transfer(data)
        current.update(task_id=str(result['task_id']), status='SUBMITTED')
        state['phase'] = 'waiting'
        save()
        return state
