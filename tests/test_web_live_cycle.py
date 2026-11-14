from contextlib import nullcontext

import pytest

from hydro_ops.web import live_cycle
from hydro_ops.web.globus_transfer import atomic_json, digest


def test_configuration_guard(tmp_path):
    (tmp_path / 'config').mkdir()
    frozen = {'production_enabled': True, 'destination_root': '/web/nwm'}
    atomic_json(tmp_path / 'config/web_daily.json', {'mode': 'publish', 'enabled': False})
    atomic_json(tmp_path / 'config/web_globus.json', frozen)
    live_cycle.configuration_guard(tmp_path, frozen)
    for changed in (dict(frozen, production_enabled=False), dict(frozen, destination_root='/other')):
        atomic_json(tmp_path / 'config/web_globus.json', changed)
        with pytest.raises(ValueError, match='configuration'):
            live_cycle.configuration_guard(tmp_path, frozen)


def test_activation_only_after_success_without_config_changes(tmp_path):
    (tmp_path / 'config').mkdir()
    initial = {'mode': 'publish', 'enabled': False}
    path = tmp_path / 'config/web_daily.json'
    atomic_json(path, initial)
    with pytest.raises(ValueError, match='activation'):
        live_cycle.activate_daily(tmp_path, initial, {'status': 'failed'})
    result = {'status': 'passed', 'remoteWrites': True}
    live_cycle.activate_daily(tmp_path, initial, result)
    with pytest.raises(ValueError, match='activation'):
        live_cycle.activate_daily(tmp_path, initial, result)


def test_publish_acceptance_and_fail_closed(tmp_path, monkeypatch):
    request = tmp_path / 'request.json'
    atomic_json(request, {'operational_mode': 'publish', 'acceptedDeployment': 'old'})
    receipt = tmp_path / 'coordinator-acceptance.json'
    atomic_json(receipt, {'status': 'passed', 'requestSha256': digest(request),
        'deploymentPlan': 'plan.json', 'fullPublication': 'full', 'upstreamPins': {'a': 'b'}})
    plan = {'source': 'full', 'previous_id': 'old', 'config': {}}
    monkeypatch.setattr(live_cycle, 'validate', lambda *a: (plan, {}))
    monkeypatch.setattr(live_cycle, 'configuration_guard', lambda *a: None)
    monkeypatch.setattr(live_cycle, 'validate_inputs', lambda *a: {'a': 'b'})
    monkeypatch.setattr(live_cycle, 'transfer_client', lambda *a: nullcontext(None))
    monkeypatch.setattr(live_cycle, 'advance', lambda *a, **k: {'phase': 'published', 'completed': []})
    assert live_cycle.publish(tmp_path, request, receipt)['status'] == 'passed'
    plan['previous_id'] = 'unexpected'
    with pytest.raises(ValueError, match='full history'):
        live_cycle.publish(tmp_path, request, receipt)
    plan['previous_id'] = 'old'
    monkeypatch.setattr(live_cycle, 'validate_inputs', lambda *a: {'a': 'changed'})
    with pytest.raises(ValueError, match='Inputs revised'):
        live_cycle.publish(tmp_path, request, receipt)
