"""Resumable, isolated Globus commissioning. Never writes the live web catalog."""
import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import requests

RESOURCE = 'transfer.api.globus.org'
TEST_PREFIX = '_hydro_ops_transfer_test'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path, value):
    """Private temporary file followed by atomic replacement (also for tokens)."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def child_path(root, relative):
    base, child = PurePosixPath(root), PurePosixPath(relative)
    if not base.is_absolute() or str(base) == '/' or '..' in base.parts:
        raise ValueError('Unsafe configured root')
    if child.is_absolute() or '..' in child.parts or not child.parts:
        raise ValueError('Unsafe relative path')
    return str(base/child)


def source_path(project, local, collection_root):
    relative = Path(local).resolve().relative_to(Path(project).resolve()).as_posix()
    return child_path(collection_root, relative)


def prepare_test(project, config):
    project = Path(project).resolve()
    run_id = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12]
    folder = project/'web/work/globus-tests'/run_id
    folder.mkdir(parents=True, exist_ok=False)
    payload = folder/'payload'
    payload.mkdir()
    atomic_json(payload/'probe.json', {'test': True, 'id': run_id, 'purpose': 'Globus/HTTPS path verification'})
    (payload/'probe.txt').write_text(f'Hydro Ops isolated transfer test {run_id}\n')
    rows = []
    for path in sorted(payload.iterdir()):
        relative = f'{TEST_PREFIX}/{run_id}/{path.name}'
        rows.append({'local': str(path.relative_to(project)), 'sha256': digest(path),
            'bytes': path.stat().st_size,
            'source': source_path(project, path, config['source_project_root']),
            'destination': child_path(config['destination_root'], relative),
            'url': urljoin(config['public_base_url'], relative)})
    plan = {'schema': 'hydro_ops_globus_test_v1', 'id': run_id, 'config': config,
            'created_at': datetime.now(UTC).isoformat(), 'files': rows}
    atomic_json(folder/'plan.json', plan)
    atomic_json(folder/'state.json', {'phase': 'planned', 'plan_sha256': digest(folder/'plan.json')})
    return folder/'plan.json'


def validate_plan(project, plan_path):
    plan_path = Path(plan_path)
    plan = json.loads(plan_path.read_text())
    state = json.loads(plan_path.with_name('state.json').read_text())
    if state['plan_sha256'] != digest(plan_path) or plan['schema'] != 'hydro_ops_globus_test_v1':
        raise ValueError('Plan changed or unsupported')
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', plan['id']):
        raise ValueError('Invalid test identifier')
    config = plan['config']
    if urlparse(config['public_base_url']).scheme != 'https':
        raise ValueError('HTTPS is required')
    if len(plan['files']) != 2:
        raise ValueError('Only two commissioning files permitted')
    for item in plan['files']:
        name = Path(item['local']).name
        if name not in ('probe.json', 'probe.txt'):
            raise ValueError('Unexpected commissioning file')
        rel = f'{TEST_PREFIX}/{plan["id"]}/{name}'
        if item['destination'] != child_path(config['destination_root'], rel):
            raise ValueError('Destination escapes isolated test namespace')
        if item['url'] != urljoin(config['public_base_url'], rel):
            raise ValueError('HTTP mapping changed')
        local = Path(project)/item['local']
        if source_path(project, local, config['source_project_root']) != item['source']:
            raise ValueError('Source mapping changed')
        if digest(local) != item['sha256'] or local.stat().st_size != item['bytes']:
            raise ValueError('Source changed after planning')
    return plan, state


@contextmanager
def transfer_client(config):
    import globus_sdk

    client_id = os.environ.get(config['client_id_env'])
    if not client_id:
        raise ValueError(f'Set {config["client_id_env"]} in the project environment')
    token_file = Path(os.path.expanduser(config['token_file']))
    if token_file.is_symlink() or token_file.stat().st_mode & 0o077:
        raise ValueError('Token file must be private and not a symlink')
    # The original utilities do not take this lock; do not run both token writers concurrently.
    with lock(token_file.with_suffix(token_file.suffix+'.lock')):
        tokens = json.loads(token_file.read_text())
        if not tokens.get('refresh_token'):
            raise ValueError('Interactive Globus login required: refresh token missing')

        def refreshed(response):
            updated = dict(tokens)
            updated.update(response.by_resource_server[RESOURCE])
            atomic_json(token_file, updated)
            tokens.update(updated)

        auth = globus_sdk.RefreshTokenAuthorizer(tokens['refresh_token'],
            globus_sdk.NativeAppAuthClient(client_id), access_token=tokens.get('access_token'),
            expires_at=tokens.get('expires_at_seconds'), on_refresh=refreshed)
        yield globus_sdk.TransferClient(authorizer=auth)


def submit_test(project, path, client):
    import globus_sdk

    path = Path(path)
    with lock(path.with_suffix('.lock')):
        plan, state = validate_plan(project, path)
        if state.get('task_id'):
            return state
        if not state.get('submission_id'):
            state['submission_id'] = client.get_submission_id()['value']
            atomic_json(path.with_name('state.json'), state)
        config = plan['config']
        data = globus_sdk.TransferData(source_endpoint=config['source_collection_id'],
            destination_endpoint=config['destination_collection_id'], submission_id=state['submission_id'],
            label='hydro-ops isolated web test '+plan['id'], sync_level='checksum',
            verify_checksum=True, preserve_timestamp=True, delete_destination_extra=False)
        for item in plan['files']:
            data.add_item(item['source'], item['destination'], recursive=False,
                          external_checksum=item['sha256'], checksum_algorithm='sha256')
        result = client.submit_transfer(data)
        state.update(task_id=str(result['task_id']), phase='submitted')
        atomic_json(path.with_name('state.json'), state)
        return state


def poll_test(project, path, client):
    path = Path(path)
    with lock(path.with_suffix('.lock')):
        _, state = validate_plan(project, path)
        task = client.get_task(state['task_id'])
        status = task['status']
        state.update(task_status=status, last_checked=datetime.now(UTC).isoformat())
        if state['phase'] != 'http_verified':
            state['phase'] = {'SUCCEEDED': 'transferred', 'FAILED': 'failed'}.get(status, 'transferring')
        # Deliberately do not persist raw API responses, OAuth details or request headers.
        atomic_json(path.with_name('state.json'), state)
        return state


def verify_http(project, path, get=requests.get):
    path = Path(path)
    with lock(path.with_suffix('.lock')):
        plan, state = validate_plan(project, path)
        if state.get('task_status') != 'SUCCEEDED':
            raise ValueError('Globus task must succeed before HTTP verification')
        for item in plan['files']:
            response = get(item['url'], timeout=30, allow_redirects=False,
                           headers={'Cache-Control': 'no-cache'})
            if response.status_code != 200 or hashlib.sha256(response.content).hexdigest() != item['sha256']:
                raise ValueError('Public HTTP content does not match accepted test payload')
        state.update(phase='http_verified', http_verified_at=datetime.now(UTC).isoformat())
        atomic_json(path.with_name('state.json'), state)
        return state


def cleanup_plan(project, path):
    """Inventory only: no remote delete API is invoked by this commissioning tool."""
    path = Path(path)
    with lock(path.with_suffix('.lock')):
        plan, state = validate_plan(project, path)
        if state['phase'] != 'http_verified':
            raise ValueError('Test must pass HTTP checks before proposing cleanup')
        result = {'action': 'review_only', 'test_id': plan['id'],
            'destination_collection_id': plan['config']['destination_collection_id'],
            'files': [{'path': f['destination'], 'sha256': f['sha256']} for f in plan['files']],
            'recursive': False}
        atomic_json(path.with_name('cleanup-plan.json'), result)
        return result
