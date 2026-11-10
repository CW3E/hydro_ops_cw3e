"""Inventory obsolete Retro repair originals; produce a review-only deletion plan.

No deletion mode. Preserve JSON evidence, candidates/windows, NRT originals,
operational tests, canonical forcing, baseline and model state.
"""
import argparse
import json
import os
import stat
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from backfill_forcing_summaries import audited
from plan_baseline_cleanup import evidence

from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def file_record(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError(f'Nonregular cleanup target: {path}')
    return {'path': str(path), 'identity': list(fingerprint(path)), 'device': s.st_dev,
            'inode': s.st_ino, 'links': s.st_nlink, 'bytes': s.st_size,
            'allocated_bytes': s.st_blocks*512}


def reclaim_estimate(entries):
    groups = defaultdict(list)
    for entry in entries:
        if entry['status'] == 'eligible_for_review':
            r = entry['delete']
            groups[(r['device'], r['inode'])].append(r)
    released = [rows[0] for rows in groups.values()
                if len({r['path'] for r in rows}) == rows[0]['links']]
    return {'unique_inodes': len(groups), 'inodes_with_all_links_in_plan': len(released),
            'reclaimable_allocated_bytes': sum(r['allocated_bytes'] for r in released),
            'reclaimable_logical_bytes': sum(r['bytes'] for r in released),
            'retained_external_link_inodes': len(groups)-len(released),
            'note': 'st_blocks estimate; filesystem snapshots/deduplication may change actual savings'}


def check(task):
    root, original, target, expected, snapshot = task
    result = {'original': str(original), 'replacement': str(target), 'status': 'blocked'}
    try:
        original.relative_to(root/'forcing/work')
        target.relative_to(root/'forcing/outputs/conus/retro/hourly')
        row = file_record(original)
        if row['identity'] != expected:
            raise ValueError('Original changed since snapshot')
        current = file_record(target)
        if (row['device'], row['inode']) == (current['device'], current['inode']):
            return result | {'status': 'retained', 'reason': 'Still linked to current production data'}
        stamp = target.name[:8]
        if not '19810101' <= stamp <= '20260418':
            return result | {'status': 'retained', 'reason': 'Outside accepted daily-PRISM repair interval'}
        before = fingerprint(target)
        audited(target, 'retro')
        manifest, manifest_proof = evidence(target.with_name(target.name+'.manifest.json'))
        publication = Path(manifest['static_envelope']['audit'])
        pub, pub_proof = evidence(publication)
        if not manifest.get('rain_repair', {}).get('nonrain_compressed_chunks_unchanged'):
            raise ValueError('Not an accepted rain-repair replacement')
        day = manifest['day']
        plan, plan_proof = evidence(publication.parent/f'{day}.plan.json')
        tx, tx_proof = evidence(publication.parent/f'{day}.transaction.json')
        _, candidate_proof = evidence(Path(plan['audit']))
        if (tx.get('status') != 'published' or pub.get('status') != 'published'
                or list(before) != tx['identity']):
            raise ValueError('Publication transaction not current')
        if fingerprint(target) != before or file_record(original) != row:
            raise ValueError('Files changed during planning')
        return result | {'status': 'eligible_for_review', 'delete': row,
                         'replacement': current, 'replacement_sha256': plan['new_sha256'],
                         'snapshot_manifest': str(snapshot),
                         'retain_evidence': [manifest_proof, pub_proof, plan_proof, tx_proof, candidate_proof],
                         'retry_dependency': 'Removing this original disables old repair/summary retry paths; retire before execution'}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return result | {'error': str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1,2,4,8), default=4)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    jobs = subprocess.check_output(['squeue', '--me', '--noheader', '--format=%i|%j|%T'], text=True)
    snapshots = sorted((root/'forcing/work').glob('prism-repair-wave*/originals/snapshot.json'))
    if len(snapshots) != 7:
        raise ValueError('Expected exactly seven historical repair wave snapshots')
    tasks, snapshot_proofs = [], []
    for path in snapshots:
        snapshot, proof = evidence(path)
        snapshot_proofs.append(proof)
        if snapshot['status'] != 'passed' or Path(snapshot['snapshot_root']).resolve() != path.parent:
            raise ValueError(f'Invalid snapshot: {path}')
        for row in snapshot['files']:
            relative = Path(row['relative'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe snapshot relative path')
            tasks.append((root, path.parent/relative,
                          root/'forcing/outputs/conus/retro/hourly'/relative, row['identity'], path))
    entries = []
    progress = args.output.with_suffix('.progress.json')
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(check, tasks, chunksize=8):
            entries.append(row)
            if len(entries) % 250 == 0:
                atomic_json(progress, {'status': 'running', 'checked': len(entries), 'total': len(tasks),
                                       'counts': dict(Counter(e['status'] for e in entries))})
    report = {'status': 'review_required', 'mode': 'plan_only_no_deletion',
              'created': datetime.now(UTC).isoformat(), 'job_id': os.environ.get('SLURM_JOB_ID'),
              'scope': 'original hourly snapshots from seven completed Retro precipitation repair waves',
              'counts': dict(Counter(e['status'] for e in entries)),
              'space': reclaim_estimate(entries), 'active_jobs_at_scan_start': jobs.splitlines(),
              'active_jobs_at_scan_end': subprocess.check_output(
                  ['squeue', '--me', '--noheader', '--format=%i|%j|%T'], text=True).splitlines(),
              'consumer_review_required': True,
              'protected': ['all JSON audits/manifests/receipts', 'canonical forcing and baselines',
                            'NRT originals', 'repair candidates/windows', 'old summary backups (next inventory phase)',
                            'operational test workspaces', 'model outputs and restarts'],
              'execution_prerequisites': ['review and retire old retry consumers',
                  'recheck active jobs and every recorded identity/evidence hash',
                  'separate deletion authorization and durable execution journal'],
              'snapshot_evidence': snapshot_proofs, 'entries': entries}
    atomic_json(args.output, report)
    atomic_json(progress, {k: v for k, v in report.items() if k not in ('entries', 'snapshot_evidence')})
    print(json.dumps({'counts': report['counts'], 'space': report['space'], 'output': str(args.output)}), flush=True)


if __name__ == '__main__':
    main()
