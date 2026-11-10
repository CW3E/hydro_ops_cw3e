"""Read-only reconciliation of revision obligations with prior publication evidence.

This is a metadata inventory, not a payload audit or authority to acknowledge
queue entries. In particular a rain-only repair cannot prove temperature current.
"""
import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.repair_transaction import atomic_json


@lru_cache(maxsize=2048)
def read(path):
    return json.loads(Path(path).read_text())


def identity_matches(expected, path):
    stat = path.stat()
    current = {'inode': stat.st_ino, 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    if isinstance(expected, list):
        return expected == [current[k] for k in ('inode', 'bytes', 'mtime_ns')]
    return bool(expected) and all(expected.get(k) == v for k, v in current.items()
                                  if k in expected) and all(k in expected for k in ('bytes', 'mtime_ns'))


def temperature_origin(manifest):
    """Follow retained pre-rain manifests; timestamps are clues, not source pins."""
    current = manifest
    visited = set()
    for _ in range(12):
        if current.get('created'):
            return current['created']
        previous = current.get('previous_manifest')
        if not previous or previous in visited:
            return None
        visited.add(previous)
        try:
            current = read(previous)
        except (OSError, ValueError):
            return None
    return None


def chronology(created, release):
    if not created or not release.get('release_date'):
        return 'unknown'
    built = datetime.fromisoformat(created).date()
    released = datetime.fromisoformat(release['release_date']).date()
    return ('release_postdates_build' if released > built
            else 'release_not_later_than_build_not_proof_of_use')


def reconcile(root, key, event):
    stream, stamp = key.split('/')
    day = datetime.fromisoformat(stamp)
    target = root/'forcing/outputs/conus'/stream/'hourly'/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    manifest = read(str(target)+'.manifest.json')
    repair = manifest.get('rain_repair', {})
    evidence = read(repair['candidate_audit']) if repair.get('candidate_audit') else {}
    published = repair.get('published_identity', manifest.get('published_identity', {}))
    publication_matches = identity_matches(published, target)
    thermal = manifest.get('temperature_refresh', {})
    thermal_verified = False
    publication_path = thermal.get('publication_acceptance')
    if thermal.get('status') == 'passed' and publication_path:
        publication = read(publication_path)
        audit_path = thermal.get('candidate_audit')
        thermal_verified = (
            publication.get('status') == 'passed'
            and identity_matches(publication.get('published_artifacts', {}).get(str(target)), target)
            and audit_path is not None
            and hashlib.sha256(Path(audit_path).read_bytes()).hexdigest()
            == thermal.get('candidate_audit_sha256')
            and read(audit_path).get('status') in ('passed', 'passed_candidate_only')
        )
        # Thermal replacement changes file identity, but its accepted transaction
        # explicitly proves preservation of the previously accepted rain payload.
        if (thermal_verified and publication.get('rain_preserved') is True
                and thermal.get('unchanged_nonthermal_payloads') is True):
            previous = read(manifest['previous_manifest'])
            original = Path(manifest['previous_manifest'].removesuffix('.manifest.json'))
            publication_matches = identity_matches(previous.get('published_identity', {}), original)
    accepted_rain = repair.get('status') in ('passed', 'published') and evidence.get('status') == 'passed'
    replay = manifest.get('replay_provenance', {})
    created = temperature_origin(manifest)
    rows = []
    for source_key, queued_signature in sorted(event['sources'].items()):
        variable, source_day = source_key.split('/')
        source_date = datetime.fromisoformat(source_day)
        source = root/'forcing/inputs/oregon_state/prism/an/4km/daily'/variable/source_date.strftime(
            f'%Y/%m/prism_{variable}_us_25m_%Y%m%d.nc')
        current = source_signature(source)
        result = 'unresolved_no_exact_source_evidence'
        if current['signature'] != queued_signature:
            result = 'queue_superseded'
        elif (variable in ('tmin', 'tmax') and thermal_verified
              and thermal.get('prism_sources', {}).get(source_key, {}).get('signature')
              == queued_signature):
            result = 'accepted_thermal_source_signature_matches'
        elif (publication_matches and replay.get('status') == 'passed'
              and replay.get('source_signatures', {}).get(source_key) == queued_signature):
            result = 'hourly_coupled_source_record_matches'
        elif variable == 'ppt' and accepted_rain and publication_matches:
            pin = evidence.get('prism_sources', {}).get(source_day, {})
            identity = [current[k] for k in ('inode', 'bytes', 'mtime_ns')]
            if pin.get('source_identity') == identity and pin.get('release') == current['release']:
                result = 'accepted_rain_source_identity_matches'
            elif pin:
                result = 'rain_source_pin_differs_requires_content_check'
            else:
                result = 'accepted_rain_repair_source_version_unrecorded'
        row = {'source': source_key, 'classification': result}
        if variable in ('tmin', 'tmax'):
            row.update(temperature_build_timestamp=created,
                       current_release_date=current['release'].get('release_date'),
                       chronology=chronology(created, current['release']))
        rows.append(row)
    return {'key': key, 'accepted_rain_repair_record': accepted_rain,
            'publication_identity_matches': publication_matches,
            'candidate_audit': repair.get('candidate_audit'), 'sources': rows,
            'thermal_publication': publication_path if thermal_verified else None,
            'queue_acknowledgement_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    queue = root/'forcing/status/prism-revisions/queue.json'
    raw = queue.read_bytes()
    state = json.loads(raw)
    rows = []
    for key, event in sorted(state['pending'].items()):
        try:
            rows.append(reconcile(root, key, event))
        except (OSError, ValueError, KeyError) as error:
            rows.append({'key': key, 'error': str(error), 'queue_acknowledgement_authorized': False})
    counts = Counter(f"{row['key'].split('/')[0]}/{source['classification']}"
                     for row in rows for source in row.get('sources', []))
    report = {'status': 'inventoried', 'created': datetime.now(UTC).isoformat(),
              'scope': 'metadata only; no scientific or downstream acceptance implied',
              'production_modified': False, 'queue_modified': False,
              'queue_snapshot_sha256': hashlib.sha256(raw).hexdigest(),
              'queue_changed_during_scan': queue.read_bytes() != raw,
              'stream_days': dict(Counter(row['key'].split('/')[0] for row in rows)),
              'source_obligation_counts': dict(counts), 'items': rows}
    atomic_json(args.output, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'items'}, indent=2))


if __name__ == '__main__':
    main()
