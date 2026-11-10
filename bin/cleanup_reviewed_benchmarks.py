"""Delete reviewed obsolete benchmark arrays from inventory 5188557, not campaigns."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import cleanup_historical_experiments as cleanup

ROOTS = (
    'forcing/work/static-mask-scaling',
    'forcing/work/static-mask-parallel-benchmark',
    'forcing/work/post2020-production-benchmark-20260914T055016',
    'forcing/work/post2020-production-benchmark-20260916T163951',
    'forcing/work/retro-prism-window-optimization-20260915T031946',
    'forcing/work/nrt-baseline-workers-20260918',
    'forcing/work/nrt-baseline-source-chunks-20260918T064744',
    'forcing/work/nrt-doubled-workers-4635991',
    'forcing/work/daily-summary-benchmark',
    'forcing/work/summary-parallel-benchmark',
    'forcing/work/stage4-downstream-benchmark-4699581',
    'forcing/work/stage4-lookback-benchmark-4699394',
)


def scheduler(root):
    result = subprocess.check_output(
        ['squeue', '--me', '--noheader', '--format=%i|%j|%T|%o|%Z'], text=True)
    allowed = {str(root/'slurm'/name) for name in (
        'extend_repaired_retro.sh', 'run_repaired_retro.sh',
        'test_operational_prism_revision.sh', 'cleanup_reviewed_benchmarks.sh')}
    for line in result.splitlines():
        fields = line.split('|')
        if len(fields) != 5 or fields[3].split()[0] not in allowed:
            raise ValueError(f'Unreviewed live job: {line}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # Reuse the exact-target, symlink/hardlink/restart guards and durable journal
    # from the previous historical cleanup, with a separately reviewed scope.
    cleanup.ROOTS = ROOTS
    snapshot = scheduler(root)
    inventory = root/'forcing/status/cleanup/historical-work-5188557.files.jsonl'
    expected = {}
    with inventory.open() as stream:
        for line in stream:
            row = json.loads(line)
            if any(Path(row['path']).is_relative_to(Path(base)) for base in ROOTS):
                expected[row['path']] = row
    entries, retained = cleanup.inventory(root)
    for item in entries + retained:
        if item['path'] not in expected:
            raise ValueError(f'New file since inventory: {item["path"]}')
        old = expected[item['path']]
        if any(item['identity'][k] != old[k] for k in ('device', 'inode', 'bytes', 'mtime_ns', 'links')):
            raise ValueError(f'Changed since inventory: {item["path"]}')
    if {item['path'] for item in entries+retained} != set(expected):
        raise ValueError('Files disappeared since inventory')
    plan = {'mode': 'reviewed_experimental_arrays_only', 'project_root': str(root),
            'reviewed_roots': list(ROOTS), 'delete': entries, 'retain': retained,
            'scheduler': snapshot, 'inventory': str(inventory),
            'inventory_sha256': hashlib.sha256(inventory.read_bytes()).hexdigest(),
            'dependency_review': 'Completed benchmark arrays only; code references inspected. '
            'Active model and operational PRISM tests do not read these roots. '
            'Retain all reports, unknown types, links, restart/input material and campaign work.'}
    cleanup.durable_json(args.plan, plan)
    print(json.dumps({'planned_files': len(entries),
                      'logical_bytes': sum(e['identity']['bytes'] for e in entries),
                      'retained_files': len(retained)}), flush=True)
    if args.execute:
        result = cleanup.execute(root, plan, args.plan.with_suffix('.journal.jsonl'), scheduler(root))
        for base in ROOTS:
            cleanup.durable_json(root/base/'CLEANUP_COMPLETED.json', {
                'status': 'historical_arrays_removed_reports_retained',
                'plan': str(args.plan.resolve()),
                'journal': str(args.plan.with_suffix('.journal.jsonl').resolve()),
                'warning': 'Historical evidence only; removed arrays require regeneration.'})
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
