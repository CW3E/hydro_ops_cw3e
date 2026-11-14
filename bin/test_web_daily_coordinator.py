"""Receipt-gated rendering and deployment planning only; never invokes Globus."""
import argparse
import json
import os
from datetime import date
from pathlib import Path

import build_web_daily_collection as collection

from hydro_ops.web.daily import validate_inputs
from hydro_ops.web.globus_transfer import atomic_json, digest, lock
from hydro_ops.web.history import merge
from hydro_ops.web.publication import prepare, raster_inventory

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('gate', 'run'))
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--gate-job')
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    if request.get('test') is not True and request.get('operational_mode') != 'publish':
        raise ValueError('Only isolated tests are enabled')
    job = os.environ['SLURM_JOB_ID']
    if not job.isdecimal():
        raise ValueError('SLURM required')
    folder = args.request.resolve().parent
    pins = validate_inputs(ROOT, request)
    if args.action == 'gate':
        atomic_json(folder / f'gate-{job}.json', {'status': 'passed', 'job_id': job,
            'requestSha256': digest(args.request), 'pins': pins})
        print(json.dumps({'status': 'passed', 'job_id': job, 'pinnedReceipts': len(pins)}))
        return
    if not args.gate_job or not args.gate_job.isdecimal():
        raise ValueError('Explicit gate job required')
    gate_path = folder / f'gate-{args.gate_job}.json'
    gate = json.loads(gate_path.read_text())
    if (gate.get('status') != 'passed' or gate.get('job_id') != args.gate_job
            or gate['requestSha256'] != digest(args.request) or gate['pins'] != pins):
        raise ValueError('Gate receipt changed or does not match request')
    output = folder / f'run-{job}'
    control = ROOT / 'web/status/daily'
    control.mkdir(parents=True, exist_ok=True)
    with lock(control / 'coordinator.lock'):
        # Recheck after waiting for a preceding web coordinator.
        if validate_inputs(ROOT, request) != pins:
            raise ValueError('Upstream receipts changed while waiting')
        collection.prepare(ROOT / request['base'], output, date.fromisoformat(request['start']),
            date.fromisoformat(request['end']), set(), [ROOT / p for p in pins] + [gate_path])
        plan = collection.read(output / 'cycle.json')
        for task in range(plan['tasks']):
            collection.worker(output, task)
        collection.finalize(output)
        if validate_inputs(ROOT, request) != pins:
            raise ValueError('Upstream publications changed during rendering')
        full = output / 'full-publication'
        history = merge(ROOT / request.get('history_base', request['base']), output / 'publication', full)
        if validate_inputs(ROOT, request) != pins:
            raise ValueError('Upstream publications changed during history merge')
        config = json.loads((ROOT / 'config/web_globus.json').read_text())
        if config.get('production_enabled') and request.get('operational_mode') != 'publish':
            raise ValueError('This test requires live transfer disabled')
        deployment = prepare(ROOT, full, raster_inventory(full), config)
        if (request.get('acceptedDeployment')
                and json.loads(deployment.read_text())['previous_id'] != request['acceptedDeployment']):
            raise ValueError('Accepted publication advanced during rendering; regenerate against new history')
        atomic_json(output / 'coordinator-acceptance.json', {'status': 'passed', 'test': request.get('test', True),
            'job_id': job, 'gateJob': args.gate_job, 'requestSha256': digest(args.request),
            'deploymentPlan': str(deployment.relative_to(ROOT)), 'remoteWrites': False,
            'upstreamPins': pins, 'reusedImages': len(plan['reused']), 'changedSources': plan['changedSources'],
            'historyMerge': history['families'], 'fullPublication': str(full.relative_to(ROOT))})
        print(json.dumps({'status': 'passed', 'deploymentPlan': str(deployment), 'remoteWrites': False}))


if __name__ == '__main__':
    main()
