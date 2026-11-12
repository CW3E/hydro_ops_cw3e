"""Activate tested two-stage selection under production locks; no forcing rewrites."""
import argparse
import fcntl
import json
import os
import subprocess
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.prism_maturity import (
    apply_queue_policy,
    implementation_fingerprint,
    input_root,
    paths,
)
from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--test-report', type=Path, required=True)
    args = p.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    accepted = json.loads(args.test_report.read_text())
    if (accepted.get('status') != 'passed' or not accepted.get('candidate_and_noop_passed')
            or accepted.get('implementation_sha256') != implementation_fingerprint(root)):
        raise ValueError('Matching isolated candidate/no-op acceptance required')
    campaign = root/'forcing/work/revision-backlog-drain/nrt-20261111'
    if not (campaign/'PAUSED.json').exists():
        raise ValueError('Old all-release campaign must remain paused')
    state = root/'forcing/status/prism-maturity'
    state.mkdir(parents=True, exist_ok=True)
    job = os.environ['SLURM_JOB_ID']
    jobs = subprocess.check_output(['squeue', '--me', '--noheader', '--format=%i|%j'], text=True)
    conflicts = [line for line in jobs.splitlines() if line.split('|')[0] != job
                 and line.split('|')[-1].startswith(('nwm-cycle-', 'prism-nrt-', 'prism-retro-', 'nrt-'))]
    if conflicts:
        raise ValueError(f'Wait for existing forcing submissions before policy cutover: {conflicts}')
    with ExitStack() as stack:
        for name in ('forcing/work/update_nwm_forcing.lock', 'forcing/status/nrt-gfs/cycle.lock',
                     'forcing/outputs/conus/.rain-repair.lock',
                     'forcing/status/prism-revisions/bounded-batch.lock',
                     'forcing/status/prism-revisions/queue.lock'):
            lock = stack.enter_context((root/name).open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        queue_path = root/'forcing/status/prism-revisions/queue.json'
        queue = json.loads(queue_path.read_text())
        atomic_json(state/f'queue-before-{job}.json', queue)
        before = dict(Counter(e['stream'] for e in queue.get('pending', {}).values()))
        today = datetime.now(UTC).date()
        sources = {}
        for offset in range(401):
            day = today-timedelta(days=offset)
            for path in paths(input_root(root, 'retro'), day):
                if path.is_file() and path.with_suffix('.release.json').is_file():
                    sources[f'{path.name.split("_")[1]}/{day}'] = source_signature(path)
        apply_queue_policy(queue, root, today, sources)
        queue['status'] = 'pending' if queue['pending'] else 'current'
        atomic_json(state/f'queue-after-{job}.json', queue)
        atomic_json(queue_path, queue)
        report = {'status': 'passed', 'policy': 'two_maturity_stages_v1', 'job': job,
                  'test_report': str(args.test_report.resolve()), 'activated_at': datetime.now(UTC).isoformat(),
                  'before_pending': before,
                  'after_pending': dict(Counter(e['stream'] for e in queue['pending'].values())),
                  'policy_dispositions': len(queue.get('policy_dispositions', {})),
                  'forcing_outputs_modified': False, 'model_runs_enabled': False,
                  'implementation_sha256': implementation_fingerprint(root)}
        atomic_json(state/f'activation-{job}.json', report)
        atomic_json(state/'activation.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
