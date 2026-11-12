"""Audit legacy payloads and staged duplicates; emit an explicit non-executing plan."""
import argparse
import json
import os
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations.station_catalog import IDENTITY
from hydro_ops.observations.station_cleanup import compare_history, legacy_payload
from hydro_ops.observations.station_conversion import digest, inside
from hydro_ops.observations.station_migration import fingerprint
from hydro_ops.observations.station_operations import active_catalog
from hydro_ops.observations.station_store import read_station


def audit_one(root_name, staged_entry, current_entry, stage_base):
    root = Path(root_name)
    result = {'station': {k: staged_entry[k] for k in IDENTITY}, 'status': 'retain_review', 'candidates': []}
    try:
        if current_entry is None:
            raise ValueError('No active replacement')
        stage = inside(Path(stage_base), staged_entry['path'])
        current = inside(active_catalog(root).parent, current_entry['path'])
        before_stage, before_current = fingerprint(stage), fingerprint(current)
        if digest(stage) != staged_entry['sha256'] or digest(current) != current_entry['sha256']:
            raise ValueError('Catalog payload checksum mismatch or concurrent update')
        receipt_path = stage.parent.parent/'receipts'/f'{stage.stem}.json'
        receipt = json.loads(receipt_path.read_text())
        if (receipt['status'] != 'accepted' or receipt['roundtrip'] != 'exact'
                or receipt['file_sha256'] != staged_entry['sha256']):
            raise ValueError('Missing exact source conversion acceptance')
        old, new = read_station(stage), read_station(current)
        if old.signature() != receipt['content_sha256']:
            raise ValueError('Staged table content signature mismatch')
        comparison = compare_history(old, new)
        result.update(comparison)
        proofs = []
        for source in receipt['sources']:
            p = inside(root, source['path'])
            original = fingerprint(p)
            if digest(p) != source['sha256'] or fingerprint(p) != original:
                raise ValueError('Legacy source/evidence changed: '+source['path'])
            if legacy_payload(source['path']):
                proofs.append({'path': source['path'], 'identity': original, 'sha256': source['sha256'],
                               'bytes': original[1], 'kind': 'legacy_processed_payload'})
        if fingerprint(stage) != before_stage or fingerprint(current) != before_current:
            raise ValueError('Replacement changed during audit')
        result.update(replacement=str(current.relative_to(root)), replacement_identity=before_current,
                      replacement_sha256=current_entry['sha256'], conversion_receipt=str(receipt_path.relative_to(root)))
        if result['status'] == 'eligible':
            result['candidates'] = proofs + [{'path': str(stage.relative_to(root)), 'identity': before_stage,
                'sha256': staged_entry['sha256'], 'bytes': before_stage[1], 'kind': 'accepted_staged_copy'}]
        result['legacy_payload_files'] = len(proofs)
        result['legacy_payload_bytes'] = sum(p['bytes'] for p in proofs)
    except Exception as error:  # noqa: BLE001 -- an audit failure must retain files
        result.update(status='retain_review', reason=f'{type(error).__name__}: {error}', candidates=[])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or (args.limit is not None and args.limit < 1):
        parser.error('Use 1–8 workers and a positive optional limit')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    work = root/'observations/work/station-cleanup'/os.environ['SLURM_JOB_ID']
    work.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    active = json.loads((root/'observations/status/stations/active.json').read_text())
    stage_catalog = inside(root, active['source_catalog'])
    if digest(stage_catalog) != active['source_catalog_sha256']:
        raise ValueError('Cutover staged catalog changed')
    stage_acceptance = json.loads(stage_catalog.with_suffix('.acceptance.json').read_text())
    if stage_acceptance['status'] != 'passed' or stage_acceptance['catalog_sha256'] != digest(stage_catalog):
        raise ValueError('Staged collection is not accepted')
    stage_entries = json.loads(stage_catalog.read_text())['entries']
    live_catalog = active_catalog(root)
    live_bytes = live_catalog.read_bytes()
    current_entries = json.loads(live_bytes)['entries']
    def key(e):
        return tuple(e[k] for k in IDENTITY)
    current = {key(e): e for e in current_entries}
    if len(current) != len(current_entries):
        raise ValueError('Duplicate live identities')
    if args.limit:
        stage_entries = stage_entries[:args.limit]
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(audit_one, str(root), e, current.get(key(e)), str(stage_catalog.parent)) for e in stage_entries]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if len(results) % 100 == 0:
                atomic_json(work/'progress.json', {'completed': len(results), 'planned': len(stage_entries),
                    'counts': dict(Counter(r['status'] for r in results)), 'elapsed_seconds': time.monotonic()-started})
    candidates = {}
    for result in results:
        for item in result['candidates']:
            item = dict(item, replacement=result['replacement'], replacement_identity=result['replacement_identity'],
                        replacement_sha256=result['replacement_sha256'], conversion_receipt=result['conversion_receipt'])
            if item['path'] in candidates:
                raise ValueError('Duplicate deletion target requires manual review')
            candidates[item['path']] = item
    # Enumerate, but never nominate, unreferenced payloads and metadata.
    inventory = []
    for base in ('observations/processed/cdec', 'observations/processed/usgs', 'observations/work/station-migration'):
        count = size = unlisted = 0
        for p in (root/base).rglob('*'):
            if p.is_file() and not p.is_symlink():
                count += 1; size += p.stat().st_size
                unlisted += str(p.relative_to(root)) not in candidates
        inventory.append({'root': base, 'files': count, 'bytes': size, 'files_not_nominated': unlisted})
    atomic_json(work/'station-audits.json', {'stations': results})
    atomic_json(work/'deletion-plan.json', {'status': 'review_required', 'execute': False,
        'policy': 'No deletion authorized by this audit. Revalidate candidate and replacement hashes/identities under station lock before any separately approved deletion.',
        'entries': sorted(candidates.values(), key=lambda e: e['path'])})
    report = {'status': 'inventory_complete_review_required', 'source_modified': False, 'deleted_files': 0,
        'stations_audited': len(results), 'limited_test': bool(args.limit),
        'counts': dict(Counter(r['status'] for r in results)), 'candidate_files': len(candidates),
        'candidate_bytes': sum(e['bytes'] for e in candidates.values()), 'inventories': inventory,
        'retained': ['provider raw acquisitions', 'station/domain metadata', 'all audit/acceptance reports',
                     'station-cycle evidence', 'unmatched/unverified/revised records'],
        'elapsed_seconds': time.monotonic()-started}
    atomic_json(work/'report.json', report)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
