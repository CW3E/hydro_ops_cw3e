import json
import uuid
from types import SimpleNamespace

import pytest

from hydro_ops.web.globus_transfer import atomic_json, digest
from hydro_ops.web.publication import advance, destination_key, prepare, raster_inventory, relative


class Client:
    def __init__(self, root):
        self.root = root
        self.files = {}
        self.calls = []
        self.status = 'SUCCEEDED'
        self.timeout = False

    def get_submission_id(self):
        return {'value': str(uuid.uuid4())}

    def submit_transfer(self, data):
        self.calls.append(data)
        if self.timeout:
            self.timeout = False
            raise TimeoutError
        for row in data['DATA']:
            self.files[row['destination_path'].removeprefix('/web/')] = (
                self.root / row['source_path'].removeprefix('/source/')).read_bytes()
        return {'task_id': str(uuid.uuid4())}

    def submit_delete(self, data):
        assert data['recursive'] is False and data['interpret_globs'] is False
        self.calls.append(data)
        for row in data['DATA']:
            self.files.pop(row['path'].removeprefix('/web/'), None)
        return {'task_id': str(uuid.uuid4())}

    def get_task(self, task_id):
        return {'status': self.status}

    def get(self, url, **kwargs):
        data = self.files.get(url.removeprefix('https://example.org/'))
        return SimpleNamespace(status_code=200 if data is not None else 404, content=data)


@pytest.fixture
def setup(tmp_path):
    config = {'source_collection_id': str(uuid.uuid4()), 'destination_collection_id': str(uuid.uuid4()),
              'source_project_root': '/source', 'destination_root': '/web',
              'public_base_url': 'https://example.org/', 'production_enabled': True}
    client = Client(tmp_path)

    def plan(generation):
        folder = tmp_path / f'g{generation}'
        folder.mkdir(exist_ok=True)
        rows = []
        for name, role in [('catalog.json', 'catalog'), ('metadata.json', 'metadata'),
                           ('old.json' if generation == 1 else 'new.json', 'payload')]:
            path = folder / name
            atomic_json(path, {'generation': generation})
            rows.append({'path': name, 'role': role, 'sha256': digest(path), 'bytes': path.stat().st_size})
        return prepare(tmp_path, folder, {'schema': 'hydro_ops_publication_inventory_v1', 'files': rows}, config)

    def step(path, **kwargs):
        return advance(tmp_path, path, client, enabled=True, get=client.get, **kwargs)

    return tmp_path, config, client, plan, step


def test_ordered_publish_owner_predecessor_and_cleanup(setup):
    root, config, client, plan, step = setup
    first, stale = plan(1), plan(2)
    step(first)
    with pytest.raises(ValueError, match='owns'):
        step(stale)
    for _ in range(6):
        state = step(first)
    assert state['phase'] == 'published'
    assert 'old.json' in client.files
    with pytest.raises(ValueError, match='Stale'):
        step(stale)
    second = plan(2)
    client.files['unrelated.json'] = b'unowned'
    for _ in range(8):
        state = step(second)
    assert state['phase'] == 'published'
    assert 'old.json' not in client.files and 'new.json' in client.files
    assert client.files['unrelated.json'] == b'unowned'
    assert len(json.loads(second.with_name('deletion-manifest.json').read_text())['paths']) == 1
    control = root / 'web/status/deployments' / destination_key(config)
    assert not (control / 'owner.json').exists()
    assert json.loads((control / 'accepted.json').read_text())['id'] == json.loads(second.read_text())['id']
    count = len(client.calls)
    step(second)
    assert len(client.calls) == count


def test_disabled_timeout_failure_and_http_gates(setup):
    root, _, client, plan, step = setup
    path = plan(1)
    with pytest.raises(ValueError, match='disabled'):
        advance(root, path, client)
    assert not client.calls
    client.timeout = True
    with pytest.raises(TimeoutError):
        step(path)
    step(path)
    assert client.calls[0]['submission_id'] == client.calls[1]['submission_id']
    client.status = 'ACTIVE'
    step(path)
    assert len(client.calls) == 2
    client.status = 'FAILED'
    assert step(path)['phase'] == 'blocked'
    step(path, retry_failed=True)
    step(path)
    assert client.calls[-1]['submission_id'] != client.calls[0]['submission_id']
    client.status = 'SUCCEEDED'
    client.files['old.json'] = b'corrupt'
    with pytest.raises(ValueError, match='Public bytes'):
        step(path)
    assert not json.loads(path.with_name('state.json').read_text())['completed']


@pytest.mark.parametrize('name', ['/a', '../a', 'a/../b', 'a//b', 'a?b', '*', 'https://host/a'])
def test_invalid_web_paths(name):
    with pytest.raises(ValueError):
        relative(name)


def test_crash_after_receipt_or_state_releases_owner(setup):
    root, config, _, plan, step = setup
    path = plan(1)
    for _ in range(7):
        step(path)
    control = root / 'web/status/deployments' / destination_key(config)
    run_id = json.loads(path.read_text())['id']
    atomic_json(control / 'owner.json', {'id': run_id})
    assert step(path)['phase'] == 'published'
    assert not (control / 'owner.json').exists()
    state = json.loads(path.with_name('state.json').read_text())
    state['phase'] = 'waiting'
    atomic_json(path.with_name('state.json'), state)
    atomic_json(control / 'owner.json', {'id': run_id})
    assert step(path)['phase'] == 'published'
    assert not (control / 'owner.json').exists()


def test_source_mutation_and_frozen_enablement(setup):
    root, config, client, plan, step = setup
    config['production_enabled'] = False
    path = plan(1)
    with pytest.raises(ValueError, match='disabled'):
        step(path)
    assert not client.calls
    config['production_enabled'] = True
    path = plan(2)
    (root / 'g2/new.json').write_text('mutated')
    with pytest.raises(ValueError, match='Frozen source'):
        step(path)
    assert not client.calls


def test_raster_inventory_excludes_internal_files(tmp_path):
    domain = tmp_path / 'cnrfc'
    domain.mkdir()
    (domain / 'image.png').write_bytes(b'png')
    atomic_json(tmp_path / 'acceptance.json', {'status': 'passed'})
    atomic_json(tmp_path / 'catalog.json', {'families': [{'href': 'cnrfc/family.json', 'images': 1}]})
    for name in ('variables.json', 'legends.json', 'plan.json'):
        atomic_json(tmp_path / name, {})
    atomic_json(domain / 'family.json', {'availability': 'availability.json',
                'timeline': 'timeline.json', 'coverage': 'coverage.json'})
    atomic_json(domain / 'availability.json', {'assets': [
        {'href': 'image.png', 'sha256': digest(domain / 'image.png')}]})
    for name in ('timeline.json', 'coverage.json'):
        atomic_json(domain / name, {})
    inventory = raster_inventory(tmp_path)
    names = {r['path']: r['role'] for r in inventory['files']}
    assert names['cnrfc/image.png'] == 'payload' and names['catalog.json'] == 'catalog'
    assert 'plan.json' not in names and 'acceptance.json' not in names
    (domain / 'image.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum mismatch'):
        raster_inventory(tmp_path)
