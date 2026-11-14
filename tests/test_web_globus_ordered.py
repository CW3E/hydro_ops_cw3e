import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydro_ops.web.ordered_test import advance, prepare


class Client:
    def __init__(self, project):
        self.project = project
        self.files = {}
        self.status = 'SUCCEEDED'
        self.calls = []
        self.fail_once = False

    def get_submission_id(self):
        return {'value': str(uuid.uuid4())}

    def submit_transfer(self, data):
        self.calls.append(data)
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError
        for row in data['DATA']:
            self.files[Path(row['destination_path']).name] = (
                self.project / row['source_path'].removeprefix('/source/')).read_bytes()
        return {'task_id': str(uuid.uuid4())}

    def submit_delete(self, data):
        self.calls.append(data)
        assert not data['recursive'] and not data['interpret_globs']
        for row in data['DATA']:
            del self.files[Path(row['path']).name]
        return {'task_id': str(uuid.uuid4())}

    def get_task(self, task_id):
        return {'status': self.status}

    def get(self, url, **kwargs):
        value = self.files.get(Path(url).name)
        return SimpleNamespace(status_code=200 if value is not None else 404, content=value)


def test_ordering_failure_resume_and_exact_cleanup(tmp_path):
    config = {'source_collection_id': str(uuid.uuid4()), 'destination_collection_id': str(uuid.uuid4()),
              'source_project_root': '/source', 'destination_root': '/web',
              'public_base_url': 'https://example.org/'}
    path = prepare(tmp_path, config)
    client = Client(tmp_path)

    def step(**kwargs):
        return advance(tmp_path, path, client, get=client.get, **kwargs)

    client.fail_once = True
    with pytest.raises(TimeoutError):
        step()
    step()
    assert client.calls[0]['submission_id'] == client.calls[1]['submission_id']
    client.status = 'ACTIVE'
    step()
    assert len(client.calls) == 2  # No duplicate submission or downstream phase.
    client.status = 'FAILED'
    assert not step()['completed']
    assert not step(retry_failed=True)['completed']
    client.status = 'SUCCEEDED'
    step()
    assert client.calls[-1]['submission_id'] != client.calls[0]['submission_id']
    client.files['record.json'] = b'bad'
    with pytest.raises(ValueError, match='HTTPS'):
        step()
    assert not json.loads(path.with_name('state.json').read_text())['completed']
    plan = json.loads(path.read_text())
    client.files['record.json'] = (tmp_path / plan['generations'][0][0]['local']).read_bytes()
    assert len(step()['completed']) == 1
    for _ in range(12):
        state = step()
    assert state['phase'] == 'passed'
    assert [(s['generation'], s['role']) for s in state['completed']] == [
        (1, 'payload'), (1, 'metadata'), (1, 'catalog'),
        (2, 'payload'), (2, 'metadata'), (2, 'catalog'), (2, 'cleanup')]
    assert json.loads(client.files['catalog.json'])['generation'] == 2
    assert 'unchanged.json' in client.files and 'obsolete.json' not in client.files
    deletion = json.loads(path.with_name('deletion-manifest.json').read_text())
    assert len(deletion['paths']) == 1 and deletion['paths'][0].endswith('/obsolete.json')
    calls = len(client.calls)
    step()
    assert len(client.calls) == calls


def test_frozen_source_change_blocks_submission(tmp_path):
    config = {'source_project_root': '/source', 'destination_root': '/web',
              'public_base_url': 'https://example.org/'}
    path = prepare(tmp_path, config)
    plan = json.loads(path.read_text())
    (tmp_path / plan['generations'][0][0]['local']).write_text('changed')
    with pytest.raises(ValueError, match='payload changed'):
        advance(tmp_path, path, Client(tmp_path))
