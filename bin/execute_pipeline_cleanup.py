"""Execute the explicitly approved 5188875 pipeline review, with fail-closed guards."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from plan_baseline_cleanup import evidence
from plan_forcing_repair_cleanup import file_record

from hydro_ops.forcing.repair_transaction import atomic_json


def validate_path(row, root):
    path = Path(row['path'])
    rel = path.relative_to(root/'forcing/work/prism-rain-pipeline')
    if (len(rel.parts) != 5 or not re.fullmatch(r'\d+(?:_\d+)?', rel.parts[0])
            or rel.parts[1] not in ('hourly', 'windows')
            or not re.fullmatch(r'\d{8}\.(LDASIN_DOMAIN1|prism_precipitation_diagnostics\.nc)', path.name)
            or rel.parts[2:4] != (path.name[:4], path.name[4:6])
            or path.resolve() != path):
        raise ValueError(f'Unexpected deletion path: {path}')
    if row['file']['links'] != 1 or file_record(path) != row['file']:
        raise ValueError(f'Changed or linked candidate: {path}')
    return path


def check_proof(proof):
    if proof['status'] != 'accepted_replacement' or file_record(Path(proof['file']['path'])) != proof['file']:
        raise ValueError('Replacement changed')
    for item in proof['retain_evidence']:
        if evidence(Path(item['path']))[1] != item:
            raise ValueError(f'Changed evidence: {item["path"]}')


def consumers(root):
    rows = subprocess.check_output(['squeue', '--me', '-h', '-o', '%i|%j|%o'], text=True)
    allowed = {str(root/'slurm'/name) for name in (
        'extend_repaired_retro.sh', 'run_repaired_retro.sh',
        'test_operational_prism_revision.sh', 'execute_pipeline_cleanup.sh')}
    for line in rows.splitlines():
        if line.split('|')[2].split()[0] not in allowed:
            raise ValueError(f'Unreviewed job: {line}')
    return rows


def check_inventory(row):
    current = file_record(Path(row['path']))
    old = row['inventory']
    if (any(current[k] != old[k] for k in ('device', 'inode', 'bytes', 'links', 'allocated_bytes'))
            or current['identity'][2] != old['mtime_ns']):
        raise ValueError(f'Changed inventory file: {row["path"]}')
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    base = root/'forcing/work/prism-rain-pipeline'
    review_path = root/'forcing/status/cleanup/pipeline-review-5188875.json'
    raw = review_path.read_bytes()
    review = json.loads(raw)
    if review['status'] != 'review_complete' or review['mode'] != 'review_only_no_deletion':
        raise ValueError('Unexpected review')
    delete = [r for r in review['entries'] if r['status'] == 'replacement_audited_review_required']
    retain = [r for r in review['entries'] if r['status'] != 'replacement_audited_review_required']
    if len(delete) != 50890 or sum(r['status'] == 'blocked_replacement' for r in retain) != 4:
        raise ValueError('Approved scope differs')
    if args.plan.exists() or args.plan.with_suffix('.journal.jsonl').exists():
        raise ValueError('Existing execution requires interrupted-run review')
    with (root/'forcing/outputs/conus/.rain-repair.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        jobs = consumers(root)
        actual = set()
        for parent, dirs, files in os.walk(base, followlinks=False):
            if any((Path(parent)/d).is_symlink() for d in dirs):
                raise ValueError('Unexpected directory symlink')
            actual.update(str(Path(parent)/name) for name in files)
        expected = {r['path'] for r in review['entries']}
        if actual != expected or len(expected) != len(review['entries']):
            raise ValueError('Pipeline tree changed since inventory')
        needed = sorted({day for r in delete for day in r['replacement_days']})
        proofs = {day: review['replacement_proofs'][day] for day in needed}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for n, _ in enumerate(pool.map(check_proof, proofs.values()), 1):
                if n % 500 == 0:
                    print(json.dumps({'replacement_proofs_checked': n}), flush=True)
            for _ in pool.map(lambda r: validate_path(r, root), delete):
                pass
        retained = []
        for row in retain:
            item = {'file': check_inventory(row)}
            if Path(row['path']).suffix == '.json':
                item['proof'] = evidence(Path(row['path']))[1]
            retained.append(item)
        # All candidate paths must remain distinct from every replacement inode.
        target_inodes = {(p['file']['device'], p['file']['inode']) for p in proofs.values()}
        if any((r['file']['device'], r['file']['inode']) in target_inodes for r in delete):
            raise ValueError('Production inode in deletion set')
        plan = {'mode': 'approved_pipeline_arrays_only', 'review': str(review_path),
                'review_sha256': hashlib.sha256(raw).hexdigest(), 'delete': delete,
                'retain': retained, 'replacement_proofs': proofs, 'reviewed_jobs': jobs}
        atomic_json(args.plan, plan)
        if not args.execute:
            return
        journal_path = args.plan.with_suffix('.journal.jsonl')
        with journal_path.open('x') as journal:
            def log(record):
                journal.write(json.dumps(record)+'\n')
                journal.flush()
                os.fsync(journal.fileno())
            log({'event': 'start', 'plan': str(args.plan), 'files': len(delete), 'jobs': consumers(root)})
            markers = [base/c/'RETIRED.json' for c in sorted({r['campaign'] for r in delete})]
            for path in markers:
                atomic_json(path, {'status': 'retiring', 'plan': str(args.plan), 'journal': str(journal_path),
                                   'reason': 'Accepted canonical replacements; historical retry inputs retired'})
            freed = 0
            for n, row in enumerate(delete, 1):
                if n == 1 or n % 500 == 0:
                    consumers(root)
                for day in row['replacement_days']:
                    check_proof(proofs[day])
                path = validate_path(row, root)
                log({'event': 'delete_intent', 'file': row['file']})
                validate_path(row, root)
                path.unlink()
                freed += row['file']['allocated_bytes']
                log({'event': 'deleted', 'path': str(path)})
                if n % 500 == 0:
                    print(json.dumps({'deleted': n, 'estimated_freed_allocated_bytes': freed}), flush=True)
            for item in retained:
                if file_record(Path(item['file']['path'])) != item['file']:
                    raise ValueError('Retained file changed')
                if 'proof' in item and evidence(Path(item['proof']['path']))[1] != item['proof']:
                    raise ValueError('Retained evidence changed')
            if any(Path(r['path']).exists() for r in delete):
                raise ValueError('Deletion targets remain')
            for path in markers:
                record = json.loads(path.read_text())
                record['status'] = 'retired'
                atomic_json(path, record)
            result = {'event': 'completed', 'deleted_files': len(delete),
                      'estimated_freed_allocated_bytes': freed, 'retained_files': len(retained),
                      'retained_evidence_verified': True}
            log(result)
            print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
