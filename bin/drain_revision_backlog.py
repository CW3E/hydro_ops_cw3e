"""Drain an explicitly frozen stream backlog via accepted two-date batches."""
import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from run_bounded_revision_queue import select_routine

from hydro_ops.forcing.repair_transaction import atomic_json


def next_action(allowed, selected, completed, maximum):
    if not set(selected) <= set(allowed):
        raise ValueError('New queue obligations outside approved campaign; replan required')
    if not selected:
        return 'deferred_or_complete'
    if completed >= maximum:
        return 'batch_limit'
    return 'run'


def explicit_selection(state, spec):
    """Frozen NRT catch-up scope; new arrivals never silently enter the campaign."""
    if spec['stream'] != 'nrt':
        raise ValueError('Open-month campaign is NRT only')
    selected = {k: v for k, v in sorted(state.get('pending', {}).items())
                if k in spec['keys'] and v['stream'] == 'nrt'}
    selected = dict(list(selected.items())[:2])
    if len(selected) == 2:
        days = sorted(v['day'] for v in selected.values())
        outside = any(v['stream'] == 'nrt' and days[0] <= v['day'] <= days[-1] and k not in selected
                      for k, v in state.get('pending', {}).items())
        if outside or (date.fromisoformat(days[-1])-date.fromisoformat(days[0])).days > 6:
            selected = dict(list(selected.items())[:1])
    return selected


def batch_command(spec, selected):
    cmd = [sys.executable, 'bin/run_bounded_revision_queue.py', '--stream', spec['stream'],
           '--limit', '2', '--execute', '--publish']
    if spec.get('defer_open_month'):
        days = sorted(e['day'] for e in selected.values())
        cmd += ['--start', days[0], '--end', days[-1], '--defer-open-month']
    else:
        cmd.append('--routine')
    if spec.get('reuse_baselines'):
        cmd.append('--reuse-baselines')
    return cmd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--batch', type=int, default=0)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    campaign = (root/args.campaign).resolve()
    if not campaign.is_relative_to(root/'forcing/work/revision-backlog-drain'):
        raise ValueError('Dedicated backlog campaign required')
    if (campaign/'PAUSED.json').exists():
        print(json.dumps({'status': 'paused', 'campaign': str(campaign),
                          'reason': 'Explicit campaign pause; no work or successor submitted'}))
        return
    spec = json.loads((campaign/'plan.json').read_text())
    if spec['stream'] not in ('nrt', 'retro') or not 1 <= spec['maximum_batches'] <= 128:
        raise ValueError('Invalid bounded campaign')
    if not 0 <= args.batch <= spec['maximum_batches']:
        raise ValueError('Batch index outside campaign bounds')
    report_path = campaign/f'batch-{args.batch:03d}.json'
    with (campaign/'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with report_path.open('x') as handle:
            handle.write('{"status":"starting"}\n')
        report = {'status': 'running', 'job': os.environ['SLURM_JOB_ID'], 'batch': args.batch}
        try:
            queue = root/'forcing/status/prism-revisions/queue.json'
            state = json.loads(queue.read_text())
            if spec.get('defer_open_month'):
                selected, deferred = explicit_selection(state, spec), []
            else:
                selected, deferred = select_routine(state, root, spec['stream'], 2)
            action = next_action(spec['keys'], selected, args.batch, spec['maximum_batches'])
            report.update(action=action, selected=list(selected), deferred=deferred)
            if action == 'run':
                subprocess.run(batch_command(spec, selected),
                               cwd=root, check=True)
                receipt = json.loads((root/f'forcing/work/bounded-revision-queue/{report["job"]}/acceptance.json').read_text())
                if receipt['status'] != 'passed' or {x['key'] for x in receipt['days']} != set(selected):
                    raise ValueError('Published batch differs from selected obligations')
                remaining = json.loads(queue.read_text())['pending']
                if any(key in remaining for key in selected):
                    raise ValueError('Accepted batch did not acknowledge exact sources')
                report.update(status='passed', publication_receipt=str(root/f'forcing/work/bounded-revision-queue/{report["job"]}/acceptance.json'))
                atomic_json(report_path, report)
                if (campaign/'PAUSED.json').exists():
                    report.update(successor_status='paused', backlog_complete=False)
                    return
                cmd = ['sbatch', '--parsable', f'--dependency=afterok:{report["job"]}',
                       f'--job-name=prism-{spec["stream"]}-backlog-batch-{args.batch+1:03d}',
                       'slurm/drain_revision_backlog.sh', '--campaign', str(campaign.relative_to(root)),
                       '--batch', str(args.batch+1)]
                report.update(successor_status='submission_uncertain', successor_command=cmd)
                atomic_json(report_path, report)
                env = dict(os.environ)
                env.pop('SBATCH_RESERVATION', None)
                job = subprocess.check_output(cmd, cwd=root, env=env, text=True).strip().split(';')[0]
                if not job.isdecimal():
                    raise ValueError('Unexpected sbatch response')
                report.update(successor_status='submitted', successor_job=job)
            else:
                pending = [k for k in state['pending'] if k in spec['keys']]
                report.update(status='complete' if not pending else 'deferred' if action == 'deferred_or_complete' else 'limit_reached',
                              remaining=pending, backlog_complete=not pending)
                atomic_json(campaign/'final.json', report)
        except BaseException as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            atomic_json(report_path, report)
            print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
