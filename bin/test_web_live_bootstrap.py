"""One-off live bootstrap/revision with retained assets; does not enable cron."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from hydro_ops.web.daily import validate_inputs
from hydro_ops.web.globus_transfer import atomic_json, digest, transfer_client
from hydro_ops.web.publication import advance, prepare, raster_inventory

ROOT = Path(__file__).resolve().parents[1]


def preflight(previous, inventory, config, get):
    """Verify legacy public metadata and forbid loss of any legacy-owned path."""
    old = raster_inventory(previous)
    missing = {r['path'] for r in old['files']} - {r['path'] for r in inventory['files']}
    if missing:
        raise ValueError('Legacy paths would be abandoned; separate cleanup review required')
    checked = []
    for row in old['files']:
        if row['role'] == 'payload':
            continue
        response = get(urljoin(config['public_base_url'], row['path']), timeout=30,
                       allow_redirects=False, headers={'Cache-Control': 'no-cache'})
        if response.status_code != 200 or response.content != (previous / row['path']).read_bytes():
            raise ValueError('Legacy public metadata differs from accepted local snapshot')
        checked.append(row['path'])
    return {'status': 'passed', 'metadataVerified': checked,
            'legacyPathsRetained': len(old['files']), 'deletions': []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    request_path = args.request.resolve()
    work = request_path.parent
    request_sha = digest(request_path)
    request = json.loads(request_path.read_text())
    config_path = work / 'live-config.json'
    config = json.loads(config_path.read_text())
    if not config.get('production_enabled') or request.get('enable_transfer') is not True:
        raise ValueError('Explicit one-off enablement required')
    routine = json.loads((ROOT / 'config/web_globus.json').read_text())
    if routine.get('production_enabled') or dict(config, production_enabled=False) != routine:
        raise ValueError('Routine publishing must stay disabled with identical destination settings')
    publication = ROOT / request['publication']
    # Require the full receipt-gated collection, not a short refresh or synthetic test.
    accepted = json.loads((publication.parent / 'coordinator-acceptance.json').read_text())
    if accepted['status'] != 'passed' or accepted['fullPublication'] != request['publication']:
        raise ValueError('Full-history coordinator acceptance required')
    if request.get('expected_previous_id'):
        upstream_request = ROOT / request['coordinator_request']
        if (digest(upstream_request) != accepted['requestSha256']
                or validate_inputs(ROOT, json.loads(upstream_request.read_text())) != accepted['upstreamPins']):
            raise ValueError('Revision input receipts changed since coordinator acceptance')
    pointer = work / 'deployment.json'
    with requests.Session() as session:
        if not pointer.exists():
            inventory = raster_inventory(publication)
            report = preflight(ROOT / request['previous_publication'], inventory, config, session.get)
            atomic_json(work / 'preflight.json', report)
            plan = prepare(ROOT, publication, inventory, config)
            frozen = json.loads(plan.read_text())
            if frozen['previous_id'] != request.get('expected_previous_id') or frozen['obsolete']:
                raise ValueError('Unexpected predecessor or deletions in one-off deployment')
            atomic_json(pointer, {'plan': str(plan.relative_to(ROOT)), 'requestSha256': request_sha})
        record = json.loads(pointer.read_text())
        if record['requestSha256'] != request_sha:
            raise ValueError('Bootstrap request changed')
        plan = ROOT / record['plan']
        if json.loads(plan.read_text())['config'] != config:
            raise ValueError('Frozen configuration changed')
        deadline = time.monotonic() + 100 * 60
        while time.monotonic() < deadline:
            if digest(request_path) != request_sha or json.loads(config_path.read_text()) != config:
                raise ValueError('Live request/configuration changed')
            with transfer_client(config) as client:
                state = advance(ROOT, plan, client, enabled=True, get=session.get)
            print(json.dumps({'phase': state['phase'], 'completed': len(state['completed']),
                              'current': state.get('current')}), flush=True)
            if state['phase'] == 'published':
                atomic_json(work / 'acceptance.json', {'status': 'passed', 'remoteWrites': True,
                    'plan': str(plan.relative_to(ROOT)), 'requestSha256': request_sha,
                    'routinePublicationEnabled': False, 'deletions': []})
                return
            if state['phase'] == 'blocked':
                raise ValueError('Transfer failed; resumable deployment retained for review')
            time.sleep(20 if state['phase'] == 'waiting' else 1)
        raise TimeoutError('Time budget exhausted; resume using the same request')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- avoid exposing SDK credential details
        raise SystemExit(f'{type(error).__name__}: live bootstrap stopped; inspect saved phase and receipts.') from None
