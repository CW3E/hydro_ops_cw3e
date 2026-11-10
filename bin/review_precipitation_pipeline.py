"""Read-only replacement/lineage review of precipitation pipeline workspace."""
import argparse
import json
import os
import re
import subprocess
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from backfill_forcing_summaries import audited
from plan_baseline_cleanup import evidence
from plan_forcing_repair_cleanup import file_record

from hydro_ops.forcing.repair_transaction import atomic_json


def replacement(task):
    root, stamp = task
    day = date.fromisoformat(stamp)
    target = root/'forcing/outputs/conus/retro/hourly'/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    try:
        before = file_record(target)
        audited(target, 'retro')
        manifest, mproof = evidence(target.with_name(target.name+'.manifest.json'))
        pubpath = Path(manifest['static_envelope']['audit'])
        pub, pproof = evidence(pubpath)
        plan, planproof = evidence(pubpath.parent/f'{day}.plan.json')
        tx, txproof = evidence(pubpath.parent/f'{day}.transaction.json')
        _, aproof = evidence(Path(plan['audit']))
        if (not manifest.get('rain_repair', {}).get('nonrain_compressed_chunks_unchanged')
                or pub['status'] != 'published' or tx['status'] != 'published'
                or tx['identity'] != before['identity'] or file_record(target) != before):
            raise ValueError('Publication proof changed or incomplete')
        return stamp, {'status': 'accepted_replacement', 'file': before,
                       'published_candidate': plan['candidate'], 'sha256': plan['new_sha256'],
                       'retain_evidence': [mproof, pproof, planproof, txproof, aproof]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return stamp, {'status': 'unresolved', 'error': str(exc)}


def replacement_days(path):
    match = re.match(r'^(\d{8})\.', path.name)
    if not match:
        return []
    token = match[1]
    day = date(int(token[:4]), int(token[4:6]), int(token[6:8]))
    if 'windows' in path.parts:
        return [str(day-timedelta(days=1)), str(day)]
    if 'hourly' in path.parts:
        return [str(day)]
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    base = root/'forcing/work/prism-rain-pipeline'
    inventory = root/'forcing/status/cleanup/historical-work-5188557.files.jsonl'
    entries, days, campaigns = [], set(), {}
    with inventory.open() as handle:
        for line in handle:
            old = json.loads(line)
            relative = Path(old['path'])
            if not relative.is_relative_to(Path('forcing/work/prism-rain-pipeline')):
                continue
            path = root/relative
            campaign = relative.parts[3]
            row = {'path': str(path), 'campaign': campaign, 'inventory': old,
                   'replacement_days': [], 'status': 'retain_evidence_or_unknown'}
            if old['category'] == 'array_review':
                try:
                    current = file_record(path)
                    if path.resolve() != path or any(current[k] != old[k] for k in (
                            'device', 'inode', 'bytes', 'links', 'allocated_bytes')) or current['identity'][2] != old['mtime_ns']:
                        raise ValueError('Array changed since inventory')
                    row['file'] = current
                    row['replacement_days'] = replacement_days(relative)
                    row['status'] = 'pending_review' if row['replacement_days'] else 'retain_unmapped_array'
                    days.update(row['replacement_days'])
                except (OSError, ValueError) as exc:
                    row.update(status='blocked_changed', error=str(exc))
            entries.append(row)
            if campaign not in campaigns:
                audit = base/campaign/'acceptance.json'
                try:
                    record, proof = evidence(audit)
                    campaigns[campaign] = {'acceptance_status': record.get('status'), 'retain_evidence': proof}
                except (OSError, ValueError) as exc:
                    campaigns[campaign] = {'acceptance_status': 'missing_or_unreadable', 'error': str(exc)}
    proofs = {}
    with ProcessPoolExecutor(max_workers=4) as pool:
        for stamp, proof in pool.map(replacement, [(root, d) for d in sorted(days)], chunksize=8):
            proofs[stamp] = proof
            if len(proofs) % 250 == 0:
                atomic_json(args.output.with_suffix('.progress.json'), {
                    'status': 'running', 'checked_dates': len(proofs), 'total_dates': len(days)})
    for row in entries:
        if row['status'] != 'pending_review':
            continue
        matching = [proofs[d] for d in row['replacement_days']]
        row['status'] = ('replacement_audited_review_required' if all(
            p['status'] == 'accepted_replacement' for p in matching) else 'blocked_replacement')
        row['exact_published_candidate_path'] = any(p.get('published_candidate') == row['path'] for p in matching)
        if any((p.get('file', {}).get('device'), p.get('file', {}).get('inode')) ==
               (row['file']['device'], row['file']['inode']) for p in matching):
            row['status'] = 'retain_production_inode'
    totals = Counter()
    for row in entries:
        totals[row['status']] += row['inventory']['allocated_bytes']
    report = {'mode': 'review_only_no_deletion', 'status': 'review_complete',
              'counts': dict(Counter(r['status'] for r in entries)),
              'allocated_bytes_by_status': dict(totals), 'campaigns': campaigns,
              'replacement_proofs': proofs, 'entries': entries,
              'scheduler': subprocess.check_output(['squeue', '--me', '-h', '-o', '%i|%j|%o'], text=True),
              'limitations': ['Prior scientific audits reused; no new full NetCDF checksums.',
                  'Window dates mapped to both adjacent calendar days; boundary gaps remain blocked.',
                  'Accepted replacement does not certify old candidate contents or retire consumers.',
                  'Do not feed this review to a deletion executor. Preserve all JSON evidence.',
                  'Inventory reflects 5188557 paths; a fresh tree comparison is required before deletion.']}
    atomic_json(args.output, report)
    atomic_json(args.output.with_suffix('.progress.json'), {
        'status': 'review_complete', 'counts': report['counts'], 'allocated_bytes_by_status': dict(totals)})
    print(json.dumps(report['counts']), flush=True)


if __name__ == '__main__':
    main()
