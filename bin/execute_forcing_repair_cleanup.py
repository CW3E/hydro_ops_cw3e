"""Execute only reviewed old-snapshot paths, retaining evidence and a durable journal."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from plan_baseline_cleanup import evidence
from plan_forcing_repair_cleanup import file_record

from hydro_ops.forcing.repair_transaction import atomic_json


def validate(entry, root, *, expected_links=None):
    old, replacement = entry['delete'], entry['replacement']
    path, target = Path(old['path']), Path(replacement['path'])
    relative = path.relative_to(root/'forcing/work')
    if (len(relative.parts) != 5 or not re.fullmatch(r'prism-repair-wave[1-7]-\d{6}-\d{6,8}', relative.parts[0])
            or relative.parts[1] != 'originals'
            or relative.parts[2:4] != (path.name[:4], path.name[4:6])
            or not re.fullmatch(r'\d{8}\.LDASIN_DOMAIN1', path.name)):
        raise ValueError(f'Unexpected deletion target: {path}')
    if target != root/'forcing/outputs/conus/retro/hourly'/Path(*relative.parts[2:]):
        raise ValueError('Unexpected replacement')
    if path.resolve() != path or target.resolve() != target:
        raise ValueError('Symlink path refused')
    current = file_record(path)
    for key in ('identity', 'device', 'inode', 'bytes', 'allocated_bytes'):
        if current[key] != old[key]:
            raise ValueError(f'Original changed: {path} ({key})')
    if current['links'] != (old['links'] if expected_links is None else expected_links):
        raise ValueError(f'Hard links changed: {path}')
    if file_record(target) != replacement:
        raise ValueError(f'Replacement changed: {target}')
    if (old['device'], old['inode']) == (replacement['device'], replacement['inode']):
        raise ValueError('Refusing current-production inode')
    for item in entry['retain_evidence']:
        _, actual = evidence(Path(item['path']))
        if actual != item:
            raise ValueError(f'Audit evidence changed: {item["path"]}')
    return path


def consumers(root):
    """Fail closed on job entry points not inspected for this cleanup."""
    allowed = {str(root/'slurm'/name) for name in (
        'extend_repaired_retro.sh', 'run_repaired_retro.sh',
        'test_operational_prism_revision.sh', 'execute_forcing_repair_cleanup.sh')}
    rows = []
    for line in subprocess.check_output(['squeue', '--me', '--noheader', '--format=%i|%j'], text=True).splitlines():
        job, name = line.split('|', 1)
        info = subprocess.run(['scontrol', 'show', 'job', '-o', job], capture_output=True, text=True, check=False)
        if info.returncode:
            raise ValueError(f'Cannot verify active job: {job}')
        match = re.search(r' Command=(.*?) WorkDir=', info.stdout)
        command = match.group(1) if match else None
        if command not in allowed:
            raise ValueError(f'Unreviewed job: {job} {name} {command}')
        rows.append({'job': job, 'name': name, 'command': command})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    raw = args.plan.read_bytes()
    plan = json.loads(raw)
    checksum = hashlib.sha256(raw).hexdigest()
    if plan['mode'] != 'plan_only_no_deletion' or plan['status'] != 'review_required':
        raise ValueError('Unexpected plan')
    if any(e['status'] not in ('retained', 'eligible_for_review') for e in plan['entries']):
        raise ValueError('Plan includes blocked entries')
    entries = [e for e in plan['entries'] if e['status'] == 'eligible_for_review']
    if len({e['delete']['path'] for e in entries}) != len(entries):
        raise ValueError('Duplicate deletion path')
    with ExitStack() as stack:
        lock = stack.enter_context((root/'forcing/outputs/conus/.rain-repair.lock').open('a'))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reviewed = consumers(root)
        for item in plan['snapshot_evidence']:
            _, actual = evidence(Path(item['path']))
            if actual != item:
                raise ValueError('Snapshot evidence changed')
        with ThreadPoolExecutor(max_workers=4) as pool:
            for n, _ in enumerate(pool.map(lambda e: validate(e, root), entries), 1):
                if n % 500 == 0:
                    print(json.dumps({'preflight_checked': n, 'total': len(entries)}), flush=True)
        reviewed = consumers(root)
        if not args.execute:
            print(json.dumps({'preflight': 'passed', 'files': len(entries)}))
            return
        if args.journal.exists():
            raise FileExistsError('Existing journal requires explicit interrupted-run review')
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        journal = stack.enter_context(args.journal.open('x'))
        def log(record):
            journal.write(json.dumps(record)+'\n')
            journal.flush()
            os.fsync(journal.fileno())
        log({'event': 'start', 'plan': str(args.plan.resolve()), 'plan_sha256': checksum,
             'at': datetime.now(UTC).isoformat(), 'reviewed_jobs': reviewed})
        for item in plan['snapshot_evidence']:
            marker = Path(item['path']).parent/'RETIRED.json'
            if marker.exists():
                raise ValueError(f'Existing retirement marker requires review: {marker}')
            atomic_json(marker, {'status': 'retiring', 'plan': str(args.plan.resolve()),
                                'plan_sha256': checksum, 'journal': str(args.journal.resolve()),
                                'reason': 'Accepted replacements; old snapshot retry retired'})
        removed_by_inode = {}
        allocated = 0
        for n, entry in enumerate(entries, 1):
            if n == 1 or n % 500 == 0:
                consumers(root)
            old = entry['delete']
            key = (old['device'], old['inode'])
            previous = removed_by_inode.get(key, 0)
            path = validate(entry, root, expected_links=old['links']-previous)
            log({'event': 'delete_intent', 'file': old, 'replacement': entry['replacement']})
            # Recheck immediately before unlink, after durable intent.
            current = file_record(path)
            if current['identity'] != old['identity'] or current['links'] != old['links']-previous:
                raise ValueError('Target changed after intent')
            path.unlink()
            removed_by_inode[key] = previous+1
            if current['links'] == 1:
                allocated += current['allocated_bytes']
            log({'event': 'deleted', 'path': str(path)})
            if n % 500 == 0:
                print(json.dumps({'deleted_files': n, 'estimated_freed_bytes': allocated}), flush=True)
        if any(Path(e['delete']['path']).exists() for e in entries):
            raise ValueError('Deletion targets remain')
        for item in plan['snapshot_evidence']:
            marker = Path(item['path']).parent/'RETIRED.json'
            record = json.loads(marker.read_text())
            record['status'] = 'retired'
            atomic_json(marker, record)
        result = {'event': 'completed', 'deleted_files': len(entries),
                  'estimated_freed_allocated_bytes': allocated, 'evidence_retained': True,
                  'at': datetime.now(UTC).isoformat()}
        log(result)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
