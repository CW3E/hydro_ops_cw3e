"""Manifest-owned, resumable in-place publication. No remote directory mirroring."""
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests

from .globus_transfer import atomic_json, child_path, digest, lock, source_path


def relative(value):
    # Restrict names to portable web paths, excluding URL queries, globs and escapes.
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', value) or any(
            p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Unsafe publication path')
    return value


def raster_inventory(folder, *, acceptance_path=None):
    """Build roles from the finalized raster contract, not extension heuristics."""
    folder = Path(folder).resolve()
    acceptance_path = Path(acceptance_path) if acceptance_path else folder / 'acceptance.json'
    if json.loads(acceptance_path.read_text())['status'] != 'passed':
        raise ValueError('Accepted publication required')
    roles = {'catalog.json': 'catalog', 'variables.json': 'metadata', 'legends.json': 'metadata'}
    if (folder / 'display-policy.json').exists():
        roles['display-policy.json'] = 'metadata'
        policy = json.loads((folder / 'display-policy.json').read_text())
        geometry = relative(policy['boundaryGeometry'])
        roles[geometry] = 'metadata'
        if digest(folder / geometry) != policy['boundaryGeometrySha256']:
            raise ValueError('Display boundary checksum mismatch')
    expected = {}
    catalog = json.loads((folder / 'catalog.json').read_text())
    for family in catalog['families']:
        family_path = relative(family['href'])
        parent = Path(family_path).parent
        definition = json.loads((folder / family_path).read_text())
        roles[family_path] = 'metadata'
        for key in ('availability', 'timeline', 'coverage'):
            name = relative((parent / relative(definition[key])).as_posix())
            roles[name] = 'metadata'
        availability = folder / parent / definition['availability']
        assets = json.loads(availability.read_text())['assets']
        if len(assets) != family['images']:
            raise ValueError('Catalog image count mismatch')
        for asset in assets:
            name = relative((parent / relative(asset['href'])).as_posix())
            if name in roles:
                raise ValueError('Duplicate asset or role collision')
            roles[name] = 'payload'
            expected[name] = asset['sha256']
    rows = []
    for name, role in sorted(roles.items()):
        path = folder / name
        path.resolve().relative_to(folder)
        checksum = digest(path)
        if name in expected and checksum != expected[name]:
            raise ValueError('Accepted asset checksum mismatch')
        rows.append({'path': name, 'role': role, 'sha256': checksum, 'bytes': path.stat().st_size})
    return {'schema': 'hydro_ops_publication_inventory_v1', 'files': rows,
            'acceptance_sha256': digest(acceptance_path)}


def validate_inventory(inventory):
    if inventory['schema'] != 'hydro_ops_publication_inventory_v1':
        raise ValueError('Unknown inventory schema')
    names = set()
    for row in inventory['files']:
        name = relative(row['path'])
        if name in names or row['role'] not in ('payload', 'metadata', 'catalog'):
            raise ValueError('Duplicate path or invalid role')
        if (row['role'] == 'catalog') != (name == 'catalog.json'):
            raise ValueError('Only root catalog.json may have catalog role')
        if not re.fullmatch('[a-f0-9]{64}', row['sha256']) or row['bytes'] < 0:
            raise ValueError('Invalid checksum or size')
        names.add(name)
    if 'catalog.json' not in names:
        raise ValueError('Root catalog required')


def destination_key(config):
    child_path(config['destination_root'], 'catalog.json')
    if urlparse(config['public_base_url']).scheme != 'https' or not config['public_base_url'].endswith('/'):
        raise ValueError('HTTPS base with trailing slash required')
    return hashlib.sha256((config['destination_collection_id'] + ':' +
                           config['destination_root'].rstrip('/')).encode()).hexdigest()


def prepare(project, folder, inventory, config, *, incremental=False):
    """Read-only remote planning; predecessor comes only from accepted local receipts."""
    project, folder = Path(project).resolve(), Path(folder).resolve()
    validate_inventory(inventory)
    folder.relative_to(project)
    key = destination_key(config)
    control = project / 'web/status/deployments' / key
    control.mkdir(parents=True, exist_ok=True)
    with lock(control / 'writer.lock'):
        previous = json.loads((control / 'accepted.json').read_text()) if (control / 'accepted.json').exists() else None
        run_id = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
        work = project / 'web/work/deployments' / run_id
        work.mkdir(parents=True)
        for row in inventory['files']:
            local = folder / row['path']
            source_path(project, local, config['source_project_root'])
            if digest(local) != row['sha256'] or local.stat().st_size != row['bytes']:
                raise ValueError('Publication differs from inventory')
        new_names = {r['path'] for r in inventory['files']}
        obsolete = sorted(r['path'] for r in previous['inventory']['files']
                          if r['path'] not in new_names) if previous else []
        plan = {'schema': 'hydro_ops_deployment_v1', 'id': run_id, 'config': config,
                'source': str(folder.relative_to(project)), 'inventory': inventory,
                'previous_id': previous['id'] if previous else None, 'obsolete': obsolete}
        if incremental:
            old = {r['path']: r['sha256'] for r in previous['inventory']['files']} if previous else {}
            plan['changed_paths'] = [r['path'] for r in inventory['files'] if old.get(r['path']) != r['sha256']]
        atomic_json(work / 'plan.json', plan)
        atomic_json(work / 'state.json', {'plan_sha256': digest(work / 'plan.json'),
                                        'completed': [], 'phase': 'planned'})
        return work / 'plan.json'


def validate(project, path):
    path = Path(path)
    plan = json.loads(path.read_text())
    state = json.loads(path.with_name('state.json').read_text())
    if plan['schema'] != 'hydro_ops_deployment_v1' or digest(path) != state['plan_sha256']:
        raise ValueError('Frozen deployment plan changed')
    validate_inventory(plan['inventory'])
    if 'changed_paths' in plan:
        names = {r['path'] for r in plan['inventory']['files']}
        if len(set(plan['changed_paths'])) != len(plan['changed_paths']) or set(plan['changed_paths']) - names:
            raise ValueError('Invalid incremental path selection')
    for name in plan['obsolete']:
        relative(name)
    source_path(project, Path(project) / plan['source'], plan['config']['source_project_root'])
    return plan, state


def advance(project, path, client, *, enabled=False, retry_failed=False, get=requests.get):
    """One step; a durable destination owner spans asynchronous task boundaries."""
    import globus_sdk

    project, path = Path(project).resolve(), Path(path)
    plan, _ = validate(project, path)
    if not enabled or not plan['config'].get('production_enabled'):
        raise ValueError('Publication disabled: explicit enablement required')
    config = plan['config']
    control = project / 'web/status/deployments' / destination_key(config)
    with lock(control / 'writer.lock'):
        plan, state = validate(project, path)
        accepted_path = control / 'accepted.json'
        accepted = json.loads(accepted_path.read_text()) if accepted_path.exists() else None
        owner_path = control / 'owner.json'
        owner = json.loads(owner_path.read_text()) if owner_path.exists() else None
        if state['phase'] == 'published':
            if owner and owner['id'] == plan['id'] and accepted and accepted['id'] == plan['id']:
                owner_path.unlink()
            return state
        if owner and owner['id'] != plan['id']:
            raise ValueError('Another deployment owns this destination')
        # Handle a crash between receipt publication and state update.
        if accepted and accepted['id'] == plan['id']:
            state['phase'] = 'published'
            atomic_json(path.with_name('state.json'), state)
            if owner:
                owner_path.unlink()
            return state
        if (accepted['id'] if accepted else None) != plan['previous_id']:
            raise ValueError('Stale predecessor: replan against latest accepted deployment')
        if 'changed_paths' in plan:
            old = {r['path']: r['sha256'] for r in accepted['inventory']['files']} if accepted else {}
            expected = {r['path'] for r in plan['inventory']['files'] if old.get(r['path']) != r['sha256']}
            if set(plan['changed_paths']) != expected:
                raise ValueError('Incremental selection differs from accepted predecessor')
        if not owner:
            atomic_json(owner_path, {'id': plan['id'], 'plan': str(path.resolve().relative_to(project))})

        def save():
            atomic_json(path.with_name('state.json'), state)

        def verify(rows):
            for row in rows:
                response = get(urljoin(config['public_base_url'], quote(row['path'], safe='/')),
                               timeout=30, allow_redirects=False, headers={'Cache-Control': 'no-cache'})
                if response.status_code != 200 or hashlib.sha256(response.content).hexdigest() != row['sha256']:
                    raise ValueError('Public bytes mismatch; downstream phases blocked')

        phases = ('payload', 'metadata', 'catalog', 'cleanup')
        role = phases[len(state['completed'])]
        selected = [r for r in plan['inventory']['files']
                    if 'changed_paths' not in plan or r['path'] in plan['changed_paths']]
        rows = [r for r in selected if r['role'] == role]
        current = state.setdefault('current', {'role': role})
        if current.get('task_id'):
            current['status'] = client.get_task(current['task_id'])['status']
            save()
            if current['status'] == 'FAILED':
                state['phase'] = 'blocked'
                if retry_failed:
                    state.setdefault('failed_attempts', []).append(dict(current))
                    state['current'] = {'role': role}
                    state['phase'] = 'ready'
                save()
                return state
            if current['status'] != 'SUCCEEDED':
                return state
            verify(rows)
            if role == 'cleanup':
                for name in plan['obsolete']:
                    response = get(urljoin(config['public_base_url'], name), timeout=30,
                                   allow_redirects=False, headers={'Cache-Control': 'no-cache'})
                    if response.status_code not in (404, 410):
                        raise ValueError('Deleted file still visible')
        elif rows or (role == 'cleanup' and plan['obsolete']):
            if role == 'cleanup':
                verify(selected)
                atomic_json(path.with_name('deletion-manifest.json'), {
                    'paths': [child_path(config['destination_root'], n) for n in plan['obsolete']],
                    'destination_collection_id': config['destination_collection_id'], 'recursive': False})
            for row in rows:
                local = project / plan['source'] / row['path']
                source_path(project, local, config['source_project_root'])
                if digest(local) != row['sha256'] or local.stat().st_size != row['bytes']:
                    raise ValueError('Frozen source changed')
            if not current.get('submission_id'):
                current['submission_id'] = client.get_submission_id()['value']
                save()
            if role == 'cleanup':
                data = globus_sdk.DeleteData(config['destination_collection_id'],
                    notify_on_succeeded=False, notify_on_failed=True, notify_on_inactive=False,
                    submission_id=current['submission_id'], recursive=False, interpret_globs=False,
                    ignore_missing=True, label=f'hydro-ops {plan["id"]} cleanup')
                for name in plan['obsolete']:
                    data.add_item(child_path(config['destination_root'], name))
                result = client.submit_delete(data)
            else:
                data = globus_sdk.TransferData(source_endpoint=config['source_collection_id'],
                    notify_on_succeeded=False, notify_on_failed=True, notify_on_inactive=False,
                    destination_endpoint=config['destination_collection_id'], submission_id=current['submission_id'],
                    label=f'hydro-ops {plan["id"]} {role}', sync_level='checksum', verify_checksum=True,
                    delete_destination_extra=False)
                for row in rows:
                    data.add_item(source_path(project, project / plan['source'] / row['path'],
                                              config['source_project_root']),
                                  child_path(config['destination_root'], row['path']), recursive=False,
                                  external_checksum=row['sha256'], checksum_algorithm='sha256')
                result = client.submit_transfer(data)
            current.update(task_id=str(result['task_id']), status='SUBMITTED')
            state['phase'] = 'waiting'
            save()
            return state
        if role == 'cleanup':
            verify(selected)
            atomic_json(accepted_path, {'id': plan['id'], 'inventory': plan['inventory'],
                                       'plan': str(path.resolve().relative_to(project)),
                                       'published_at': datetime.now(UTC).isoformat()})
        state['completed'].append(dict(current))
        del state['current']
        state['phase'] = 'published' if role == 'cleanup' else 'ready'
        save()
        if role == 'cleanup':
            owner_path.unlink()
        return state
