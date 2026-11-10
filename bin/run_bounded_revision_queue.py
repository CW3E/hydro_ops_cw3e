"""Commission a small real queue batch; no cron activation or unbounded catch-up."""
import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from hydro_ops.forcing.prism_replay import plan
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temporal_summary import next_month, summary_path


def missing_publication_context(root, stream, day):
    """Routine replay replaces existing products; incomplete months stay queued."""
    from datetime import timedelta
    days = (day-timedelta(days=1), day)
    months = sorted({d.replace(day=1) for d in days})
    required = []
    for domain in ('conus', 'cnrfc', 'b120', 'ucrb', 'yampa'):
        base = root/'forcing/outputs'/domain/stream
        required += [base/'hourly'/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')]
        required += [summary_path(base/'daily', d) for d in days]
        required += [summary_path(base/'monthly', m, monthly=True) for m in months]
    base = root/'forcing/outputs/conus'/stream
    required += [base/'hourly'/(day+timedelta(days=i)).strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                 for i in (-1, 1)]
    d = months[0]
    while d < next_month(months[-1]):
        if d not in days:
            required.append(summary_path(base/'daily', d))
        d += timedelta(days=1)
    return [str(p) for p in required if not p.is_file()]


def select_routine(state, root, stream, limit):
    """Select at most two eligible events, retaining all deferred obligations."""
    if stream not in ('nrt', 'retro') or not 1 <= limit <= 2:
        raise ValueError('Routine replay allows one or two dates in one stream')
    selected, deferred = {}, []
    for key, event in sorted(state.get('pending', {}).items()):
        if event['stream'] != stream:
            continue
        item = plan({'pending': {key: event}}, root)['items'][0]
        missing = missing_publication_context(root, stream, date.fromisoformat(event['day']))
        if item['status'] == 'blocked_sources' or missing:
            deferred.append({'key': key, 'reason': 'blocked_sources' if item['status'] == 'blocked_sources'
                             else 'incomplete_publication_context', 'missing_context': missing})
            continue
        selected[key] = event
        if len(selected) == limit:
            break
    return selected, deferred


def select_pending(state, stream, start, end, limit):
    if stream not in ('nrt', 'retro') or not 1 <= limit <= 2 or not 0 <= (end-start).days <= 6:
        raise ValueError('Commissioning allows at most two dates within a seven-day explicit window')
    selected = {}
    for key, event in sorted(state.get('pending', {}).items()):
        if event['stream'] == stream and start <= date.fromisoformat(event['day']) <= end:
            selected[key] = event
            if len(selected) == limit:
                break
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stream', required=True, choices=('nrt', 'retro'))
    parser.add_argument('--start', type=date.fromisoformat)
    parser.add_argument('--end', type=date.fromisoformat)
    parser.add_argument('--routine', action='store_true', help='Select up to two eligible queued dates at execution')
    parser.add_argument('--limit', type=int, default=2)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.routine and (args.start or args.end):
        parser.error('--routine cannot be combined with explicit dates')
    if not args.routine and (not args.start or not args.end):
        parser.error('Supply --routine or both --start and --end')
    if args.publish and not args.execute:
        parser.error('--publish requires --execute')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    queue_path = root/'forcing/status/prism-revisions/queue.json'
    state = json.loads(queue_path.read_text())
    deferred = []
    if args.routine:
        selected, deferred = select_routine(state, root, args.stream, args.limit)
    else:
        selected = select_pending(state, args.stream, args.start, args.end, args.limit)
    planned = plan({'pending': selected}, root)
    planned.update(deferred=deferred, routine=args.routine,
                   pending_in_stream=sum(e['stream'] == args.stream for e in state.get('pending', {}).values()))
    if not args.execute:
        print(json.dumps(planned, indent=2))
        return
    job = os.environ['SLURM_JOB_ID']
    campaign = root/'forcing/work/bounded-revision-queue'/job
    campaign.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'job': job, 'publish': args.publish, 'days': [], 'plan': planned}
    # Serialize candidate building/publication batches; per-file publication locks remain in force.
    lock_path = root/'forcing/status/prism-revisions/bounded-batch.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if not planned['items'] and not args.routine:
                raise ValueError('No pending dates selected; commissioning must exercise real work')
            for index, item in enumerate(planned['items']):
                if item['status'] == 'blocked_sources':
                    raise ValueError('Selected revision has missing or superseded sources')
                current = json.loads(queue_path.read_text()).get('pending', {}).get(item['key'])
                if current != selected[item['key']]:
                    raise ValueError('Selected queue event changed before execution')
                workspace_id = f'{job}{index+1:02d}'
                workspace = root/'forcing/work/isolated-revision-replay'/workspace_id
                command = [sys.executable, 'bin/test_isolated_revision_replay.py', '--case',
                           'daily' if args.stream == 'nrt' else 'monthly', '--day', item['day'],
                           '--workspace-id', workspace_id]
                subprocess.run(command, check=True)
                subprocess.run(command, check=True)  # unchanged acceptance gate
                if args.publish:
                    publication = [sys.executable, 'bin/publish_bounded_prism_replay.py',
                                   '--workspace', str(workspace), '--stream', args.stream,
                                   '--day', item['day'], '--publish']
                    subprocess.run(publication, check=True)
                    subprocess.run(publication, check=True)
                report['days'].append({'key': item['key'], 'workspace': str(workspace),
                                       'status': 'published' if args.publish else 'candidate_accepted'})
                atomic_json(campaign/'acceptance.json', report)
            report['status'] = 'passed'
            remaining = json.loads(queue_path.read_text()).get('pending', {})
            report['pending_remaining'] = sum(e['stream'] == args.stream for e in remaining.values())
            report['backlog_complete'] = report['pending_remaining'] == 0
        except BaseException as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            atomic_json(campaign/'acceptance.json', report)
            print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
