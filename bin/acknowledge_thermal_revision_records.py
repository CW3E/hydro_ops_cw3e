"""Reconcile completed thermal/rain repairs without rebuilding forcing.

Dry-run by default. Acknowledge only exact current source versions, an accepted
thermal publication, and unchanged identities for every published artifact.
Unproven legacy obligations remain queued, even when numerical spot checks pass.
"""
import argparse
import fcntl
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from reconcile_prism_revision_records import identity_matches, read, reconcile

from hydro_ops.forcing.prism_revision_queue import acknowledge
from hydro_ops.forcing.repair_transaction import atomic_json

PROVEN = {'accepted_thermal_source_signature_matches',
          'accepted_rain_source_identity_matches', 'hourly_coupled_source_record_matches'}


def publication_valid(path):
    report = read(path)
    artifacts = report.get('published_artifacts', {})
    if (report.get('status') != 'passed' or not artifacts
            or report.get('rain_preserved') is not True
            or report.get('summary_nonthermal_values_preserved') is not True):
        return False
    # Include metadata and all domains/resolutions, not just the CONUS hourly file.
    return all(identity_matches(pin, Path(name)) for name, pin in artifacts.items())


def eligible(row):
    return (bool(row.get('thermal_publication')) and bool(row.get('sources'))
            and all(s['classification'] in PROVEN for s in row['sources']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    directory = root/'forcing/status/prism-revisions'
    queue = directory/'queue.json'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError('Use a new report path; preserve previous reconciliation evidence')
    with (directory/'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        raw = queue.read_bytes()
        state = json.loads(raw)
        rows, accepted, publications = [], [], {}
        for key, event in sorted(state['pending'].items()):
            try:
                row = reconcile(root, key, event)
                if eligible(row):
                    path = row['thermal_publication']
                    if path not in publications:
                        publications[path] = publication_valid(path)
                    if publications[path]:
                        accepted.append(key)
                rows.append(row)
            except (OSError, ValueError, KeyError, TypeError) as error:
                rows.append({'key': key, 'error': str(error)})
        report = {'status': 'planned', 'created': datetime.now(UTC).isoformat(),
                  'queue_snapshot_sha256': hashlib.sha256(raw).hexdigest(),
                  'eligible': accepted, 'publication_checks': publications, 'items': rows,
                  'remaining': dict(Counter(k.split('/')[0] for k in state['pending']
                                            if k not in accepted)), 'queue_modified': False}
        atomic_json(args.output, report)
        if args.apply:
            # Preserve a complete recoverable queue snapshot before mutation.
            atomic_json(args.output.with_suffix('.queue-before.json'), state)
            for key in accepted:
                assert acknowledge(state, key, state['pending'][key]['sources'],
                                   {'status': 'passed', 'hourly': True,
                                    'summaries': True, 'subsets': True})
            if queue.read_bytes() != raw:
                raise RuntimeError('Queue changed despite lock; refusing replacement')
            if accepted:
                atomic_json(queue, state)
            report.update(status='applied', queue_modified=bool(accepted))
            atomic_json(args.output, report)
        print(json.dumps({k: v for k, v in report.items() if k != 'items'}, indent=2))


if __name__ == '__main__':
    main()
