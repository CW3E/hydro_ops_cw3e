"""Record PRISM revisions affecting both streams; no production writes."""
import argparse
import fcntl
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.prism_maturity import apply_queue_policy, policy
from hydro_ops.forcing.prism_replay import plan
from hydro_ops.forcing.prism_revision_queue import enqueue, source_signature
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lookback-days', type=int, default=400)
    args = parser.parse_args()
    if args.lookback_days < 1:
        parser.error('Lookback must be positive')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    directory = root/'forcing/status/prism-revisions'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/'queue.json'
    with (directory/'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {}
        sources = {}
        today = datetime.now(UTC).date()
        for offset in range(args.lookback_days+1):
            day = today-timedelta(days=offset)
            for var in ('ppt', 'tmin', 'tmax'):
                source = root/'forcing/inputs/oregon_state/prism/an/4km/daily'/var/day.strftime(
                    f'%Y/%m/prism_{var}_us_25m_%Y%m%d.nc')
                if source.exists() and source.with_suffix('.release.json').exists():
                    sources[f'{var}/{day}'] = source_signature(source)
        def exists(stream, day):
            return (root/'forcing/outputs/conus'/stream/'hourly'/day.strftime(
                '%Y/%m/%Y%m%d.LDASIN_DOMAIN1')).exists()
        if policy(root).get('enabled'):
            apply_queue_policy(state, root, today, sources)
        else:
            enqueue(state, sources, exists)
        state.update(scanned_at=datetime.now(UTC).isoformat(), lookback_days=args.lookback_days,
                     status='pending' if state['pending'] else 'current',
                     automatic_replay_enabled=False,
                     replay_policy='separate_bounded_consumer_after_normal_cycle_publication')
        atomic_json(path, state)
        atomic_json(directory/f"replay-plan-{os.environ.get('SLURM_JOB_ID','manual')}.json",
                    plan(state, root))
        atomic_json(directory/f"scan-{os.environ.get('SLURM_JOB_ID','manual')}.json",
                    {'sources': len(sources), 'pending_days': len(state['pending']),
                     'status': 'scanned', 'production_modified': False,
                     'automatic_replay_enabled': False})
        print(f"PRISM revision queue: {len(state['pending'])} pending stream-days; scanner does not publish")


if __name__ == '__main__':
    main()
