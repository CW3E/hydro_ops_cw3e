"""Read-only production-queue projection plus isolated pinned-release candidate test."""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.prism_maturity import (
    apply_queue_policy,
    day_ready,
    implementation_fingerprint,
    input_root,
    paths,
)
from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', action='store_true')
    args = p.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    work = root/'forcing/work/prism-maturity-test'/job
    work.mkdir(parents=True, exist_ok=False)
    today = datetime.now(UTC).date()
    report = {'status': 'running', 'job': job, 'production_modified': False, 'live_queue_modified': False}
    report['implementation_sha256'] = implementation_fingerprint(root)
    try:
        (work/'config').mkdir()
        (work/'config/prism_revision_policy.toml').write_text(
            'enabled = true\nnrt_window_days = 11\nnrt_grid_count = 2\nretro_minimum_grid_count = 8\n')
        archive = input_root(work, 'retro')
        archive.parent.mkdir(parents=True)
        archive.symlink_to(input_root(root, 'retro'))
        (work/'forcing/outputs').symlink_to(root/'forcing/outputs', target_is_directory=True)
        queue = json.loads((root/'forcing/status/prism-revisions/queue.json').read_text())
        sources = {}
        for offset in range(401):
            d = today-timedelta(days=offset)
            for path in paths(input_root(root, 'retro'), d):
                if path.is_file() and path.with_suffix('.release.json').is_file():
                    sources[f'{path.name.split("_")[1]}/{d}'] = source_signature(path)
        report['before_pending'] = dict(Counter(e['stream'] for e in queue.get('pending', {}).values()))
        apply_queue_policy(queue, work, today, sources)
        atomic_json(work/'projected-queue.json', queue)
        report['projected_pending'] = dict(Counter(e['stream'] for e in queue['pending'].values()))
        report['policy_dispositions'] = len(queue.get('policy_dispositions', {}))
        eligible = sorted(e['day'] for e in queue['pending'].values()
                          if e['stream'] == 'nrt' and day_ready(work, date.fromisoformat(e['day']), 'nrt'))
        report['eligible_nrt_days'] = eligible
        if args.candidate:
            if not eligible:
                raise ValueError('No complete second-release NRT candidate available; do not activate')
            day = eligible[-1]
            cmd = [sys.executable, 'bin/test_isolated_revision_replay.py', '--case', 'daily',
                   '--day', day, '--workspace-id', job+'01', '--defer-open-month',
                   '--prism-source-root', str(input_root(work, 'nrt')),
                   '--baseline-reuse-root', str(root/'forcing/outputs/conus/baseline/hourly')]
            subprocess.run(cmd, cwd=root, check=True)
            subprocess.run(cmd, cwd=root, check=True)
            report['candidate_day'] = day
            report['candidate_workspace'] = str(root/'forcing/work/isolated-revision-replay'/(job+'01'))
            report['candidate_and_noop_passed'] = True
        if implementation_fingerprint(root) != report['implementation_sha256']:
            raise ValueError('Implementation changed during test')
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        atomic_json(work/'acceptance.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
