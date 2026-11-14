"""Guarded completion of an accepted daily collection's live deployment."""
import json
import time

import requests

from .daily import validate_inputs
from .globus_transfer import atomic_json, digest, transfer_client
from .publication import advance, validate


def configuration_guard(root, frozen):
    daily = json.loads((root / 'config/web_daily.json').read_text())
    current = json.loads((root / 'config/web_globus.json').read_text())
    if daily.get('mode') != 'publish' or not current.get('production_enabled') or current != frozen:
        raise ValueError('Live configuration disabled or changed')


def activate_daily(root, expected, result):
    """One-time commissioning gate; never overwrite a concurrently edited config."""
    path = root / 'config/web_daily.json'
    if (result.get('status') != 'passed' or result.get('remoteWrites') is not True
            or expected.get('enabled') is not False or expected.get('mode') != 'publish'
            or json.loads(path.read_text()) != expected):
        raise ValueError('Daily activation requires passed live test and unchanged disabled configuration')
    atomic_json(path, dict(expected, enabled=True))


def publish(root, request_path, acceptance_path, *, timeout=6000):
    accepted = json.loads(acceptance_path.read_text())
    request = json.loads(request_path.read_text())
    if (accepted.get('status') != 'passed' or request.get('operational_mode') != 'publish'
            or digest(request_path) != accepted['requestSha256']):
        raise ValueError('Accepted live coordinator request required')
    plan_path = root / accepted['deploymentPlan']
    plan, _ = validate(root, plan_path)
    if (plan['source'] != accepted['fullPublication']
            or plan['previous_id'] != request['acceptedDeployment']):
        raise ValueError('Deployment not based on accepted full history')
    # Scientific input identities must still match immediately before transfer.
    if validate_inputs(root, request) != accepted['upstreamPins']:
        raise ValueError('Inputs revised after rendering')
    deadline = time.monotonic() + timeout
    with requests.Session() as session:
        while time.monotonic() < deadline:
            configuration_guard(root, plan['config'])
            with transfer_client(plan['config']) as client:
                state = advance(root, plan_path, client, enabled=True, get=session.get)
            print(json.dumps({'deploymentPhase': state['phase'],
                              'completed': len(state['completed'])}), flush=True)
            if state['phase'] == 'published':
                result = {'status': 'passed', 'remoteWrites': True,
                          'plan': accepted['deploymentPlan'], 'requestSha256': digest(request_path)}
                atomic_json(acceptance_path.parent / 'live-acceptance.json', result)
                return result
            if state['phase'] == 'blocked':
                raise ValueError('Failed transfer requires review before retry')
            time.sleep(20 if state['phase'] == 'waiting' else 1)
    raise TimeoutError('Live deployment incomplete; retain plan for resumption')
