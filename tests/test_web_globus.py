import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydro_ops.web.globus_transfer import (
    atomic_json,
    child_path,
    cleanup_plan,
    poll_test,
    prepare_test,
    source_path,
    submit_test,
    transfer_client,
    validate_plan,
    verify_http,
)


@pytest.fixture
def setup(tmp_path):
    config = {'source_collection_id': '63ab9e0e-2049-4aff-bfec-5dfb64aadc11',
        'destination_collection_id': 'de258060-df3f-4eaa-b10e-3799d6e6604f',
        'source_project_root': '/source/project', 'destination_root': '/website/nwm',
        'public_base_url': 'https://example.org/nwm/', 'client_id_env': 'TEST_GLOBUS_CLIENT',
        'token_file': str(tmp_path/'tokens.json')}
    plan = prepare_test(tmp_path, config)
    return tmp_path, config, plan


class Client:
    def __init__(self):
        self.calls = 0
        self.status = 'ACTIVE'
        self.payloads = []
        self.fail_once = False

    def get_submission_id(self):
        self.calls += 1
        return {'value': 'e18363c8-14d0-11ef-989e-0affc319aa91'}

    def submit_transfer(self, payload):
        assert (payload['notify_on_succeeded'], payload['notify_on_failed'], payload['notify_on_inactive']) == (False, True, False)
        self.payloads.append(payload)
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError('simulated uncertain submission')
        return {'task_id': 'test-task'}

    def get_task(self, task_id):
        assert task_id == 'test-task'
        return {'status': self.status}


@pytest.mark.parametrize('root,relative', [('/', 'a'), ('/safe', '../live'), ('/safe', '/live'),
                                          ('/safe/../live', 'x'), ('relative', 'x')])
def test_paths_reject_escape(root, relative):
    with pytest.raises(ValueError):
        child_path(root, relative)


def test_prepare_and_source_mapping(setup):
    project, _, path = setup
    plan, state = validate_plan(project, path)
    assert state['phase'] == 'planned'
    assert len(plan['files']) == 2
    assert all('/_hydro_ops_transfer_test/' in f['destination'] for f in plan['files'])
    assert source_path(project, project/'web/a', '/source/project') == '/source/project/web/a'
    with pytest.raises(ValueError):
        source_path(project, project.parent/'outside', '/source/project')


def test_config_preserves_http_filesystem_alias():
    config = json.loads((Path(__file__).parents[1]/'config/web_globus.json').read_text())
    assert config['destination_root'].endswith('/wrf_hydro/hydro_ops/nwm')
    assert config['public_base_url'] == 'https://cw3e.ucsd.edu/hydro/hydro_ops/nwm/'
    assert isinstance(config['production_enabled'], bool)
    if config['production_enabled']:
        daily = json.loads((Path(__file__).parents[1]/'config/web_daily.json').read_text())
        assert daily['mode'] == 'publish'


def test_mutated_plan_or_source_rejected(setup):
    project, _, path = setup
    plan, _ = validate_plan(project, path)
    local = project/plan['files'][0]['local']
    local.write_text('changed')
    with pytest.raises(ValueError, match='Source changed'):
        validate_plan(project, path)
    plan['files'][0]['destination'] = '/website/nwm/catalog.json'
    atomic_json(path, plan)
    with pytest.raises(ValueError, match='Plan changed'):
        validate_plan(project, path)


def test_submit_resume_status_http_and_cleanup(setup):
    project, _, path = setup
    client = Client()
    client.fail_once = True
    with pytest.raises(TimeoutError):
        submit_test(project, path, client)
    state = submit_test(project, path, client)
    assert state['task_id'] == 'test-task' and client.calls == 1
    assert client.payloads[0]['submission_id'] == client.payloads[1]['submission_id']
    assert client.payloads[1]['verify_checksum'] is True
    assert client.payloads[1]['delete_destination_extra'] is False
    assert all(not f['recursive'] for f in client.payloads[1]['DATA'])
    submit_test(project, path, client)
    assert len(client.payloads) == 2
    assert poll_test(project, path, client)['phase'] == 'transferring'
    with pytest.raises(ValueError):
        verify_http(project, path)
    with pytest.raises(ValueError):
        cleanup_plan(project, path)
    client.status = 'SUCCEEDED'
    assert poll_test(project, path, client)['phase'] == 'transferred'
    plan, _ = validate_plan(project, path)
    payloads = {f['url']: (project/f['local']).read_bytes() for f in plan['files']}

    def get(url, **kwargs):
        assert kwargs['allow_redirects'] is False
        return SimpleNamespace(status_code=200, content=payloads[url])

    assert verify_http(project, path, get)['phase'] == 'http_verified'
    cleanup = cleanup_plan(project, path)
    assert cleanup['action'] == 'review_only' and cleanup['recursive'] is False
    assert len(cleanup['files']) == 2


def test_failed_task_and_http_mismatch(setup):
    project, _, path = setup
    client = Client()
    submit_test(project, path, client)
    client.status = 'FAILED'
    assert poll_test(project, path, client)['phase'] == 'failed'
    client.status = 'SUCCEEDED'
    poll_test(project, path, client)
    with pytest.raises(ValueError, match='HTTP content'):
        verify_http(project, path, lambda *a, **k: SimpleNamespace(status_code=200, content=b'wrong'))
    assert json.loads(path.with_name('state.json').read_text())['phase'] == 'transferred'


def test_credentials_missing_or_public_file_rejected(setup, monkeypatch):
    _, config, _ = setup
    monkeypatch.delenv(config['client_id_env'], raising=False)
    with pytest.raises(ValueError, match='Set TEST_GLOBUS_CLIENT'), transfer_client(config):
        pytest.fail('No client should be constructed')
    monkeypatch.setenv(config['client_id_env'], 'dummy')
    token = Path(config['token_file'])
    token.write_text('{}')
    token.chmod(0o644)
    with pytest.raises(ValueError, match='private'), transfer_client(config):
        pytest.fail('No client should be constructed')


def test_atomic_json_permissions_and_failure(tmp_path, monkeypatch):
    path = tmp_path/'tokens.json'
    atomic_json(path, {'example': 'old'})
    assert path.stat().st_mode & 0o777 == 0o600

    def fail(*args):
        raise OSError('replacement failed')

    monkeypatch.setattr('hydro_ops.web.globus_transfer.os.replace', fail)
    with pytest.raises(OSError):
        atomic_json(path, {'example': 'new'})
    assert json.loads(path.read_text()) == {'example': 'old'}
    assert list(tmp_path.iterdir()) == [path]
