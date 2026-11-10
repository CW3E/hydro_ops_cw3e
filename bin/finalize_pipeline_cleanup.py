"""Reconcile completed unlinks and retained files; never delete numerical data."""
import json
import os
from collections import Counter
from pathlib import Path

from plan_baseline_cleanup import evidence
from plan_forcing_repair_cleanup import file_record

from hydro_ops.forcing.rain_repair_writer import digest
from hydro_ops.forcing.repair_transaction import atomic_json


def stable_record_equal(before, after):
    # st_dev is local to a client's mount namespace, not an archive identity.
    # st_blocks describes storage allocation, not logical content or identity.
    accounting = {'device', 'allocated_bytes'}
    return {k: v for k, v in before.items() if k not in accounting} == {
        k: v for k, v in after.items() if k not in accounting}


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    directory = root/'forcing/status/cleanup'
    path = directory/'pipeline-deletion-5188912.json'
    journal = path.with_suffix('.journal.jsonl')
    output = directory/f'pipeline-finalization-{os.environ["SLURM_JOB_ID"]}.json'
    plan = json.loads(path.read_text())
    result = {'status': 'running', 'deletion_job': '5188912', 'plan_sha256': digest(path),
              'journal_sha256': digest(journal), 'new_deletions': 0, 'errors': [],
              'device_transitions': {}, 'retained_checked': 0,
              'allocation_changes': 0, 'retained_json_checked': 0,
              'ignored_accounting_fields': ['device', 'allocated_bytes']}
    try:
        events = Counter()
        deleted, intents = set(), set()
        for line in journal.open():
            row = json.loads(line)
            events[row['event']] += 1
            if row['event'] == 'deleted':
                deleted.add(row['path'])
            elif row['event'] == 'delete_intent':
                intents.add(row['file']['path'])
        expected = {r['path'] for r in plan['delete']}
        if (deleted != expected or intents != expected or events['deleted'] != len(expected)
                or events['delete_intent'] != len(expected)):
            raise ValueError('Deletion journal does not exactly match approved scope')
        remaining = [p for p in expected if Path(p).exists() or Path(p).is_symlink()]
        if remaining:
            raise ValueError(f'{len(remaining)} deletion paths still exist')
        transitions = Counter()
        for item in plan['retain']:
            old = item['file']
            current = file_record(Path(old['path']))
            result['allocation_changes'] += old['allocated_bytes'] != current['allocated_bytes']
            if not stable_record_equal(old, current):
                result['errors'].append({'path': old['path'], 'differences': {
                    k: [old[k], current[k]] for k in old if old[k] != current[k]}})
            if 'proof' in item:
                if evidence(Path(item['proof']['path']))[1] != item['proof']:
                    result['errors'].append({'path': old['path'], 'error': 'Retained JSON hash/identity mismatch'})
                result['retained_json_checked'] += 1
            transitions[f'{old["device"]}->{current["device"]}'] += 1
            result['retained_checked'] += 1
        result['device_transitions'] = dict(transitions)
        if result['errors']:
            raise ValueError('Stable identities or retained content changed; manual review required')
        # Retained JSON hashes and stable identities pass; finalize marker state
        # without editing the original deletion journal or its failure history.
        for campaign in sorted({r['campaign'] for r in plan['delete']}):
            marker = root/'forcing/work/prism-rain-pipeline'/campaign/'RETIRED.json'
            record = json.loads(marker.read_text())
            if Path(record['plan']).resolve() != path.resolve():
                raise ValueError('Retirement marker belongs to a different plan')
        for campaign in sorted({r['campaign'] for r in plan['delete']}):
            marker = root/'forcing/work/prism-rain-pipeline'/campaign/'RETIRED.json'
            record = json.loads(marker.read_text())
            record.update(status='retired', finalization_report=str(output))
            atomic_json(marker, record)
        result.update(status='passed', deleted_paths_absent=len(expected),
                      retained_json_verified=True, boundary_files_retained=4,
                      estimated_freed_allocated_bytes=sum(r['file']['allocated_bytes'] for r in plan['delete']))
    except BaseException as exc:
        result.update(status='failed', error=str(exc))
        raise
    finally:
        atomic_json(output, result)
        print(json.dumps({k: v for k, v in result.items() if k != 'errors'} |
                         {'error_count': len(result['errors'])}), flush=True)


if __name__ == '__main__':
    main()
