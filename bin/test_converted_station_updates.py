"""Live updates on isolated copies of accepted converted station histories."""
import argparse
import calendar
import json
import os
import shutil
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations.station_catalog import (
    IDENTITY,
    read_catalog,
    read_member,
    write_catalog,
)
from hydro_ops.observations.station_conversion import digest
from hydro_ops.observations.station_store import read_station
from hydro_ops.observations.station_updates import acquire_window, refresh_station


def select_entries(entries):
    selected = {}
    for entry in sorted(entries, key=lambda e: e['station_id']):
        key = tuple(entry[k] for k in ('provider', 'product', 'sensor', 'resolution'))
        selected.setdefault(key, entry)
    return list(selected.values())


def windows(entry, end):
    if entry['resolution'] != 'monthly':
        return [((end-timedelta(days=18)).isoformat(), end.isoformat())]
    last = end.replace(day=1)-timedelta(days=1)
    first = (last.replace(day=1)-timedelta(days=1)).replace(day=1)
    result = [(first.isoformat(), last.isoformat())]
    if entry['product'] == 'snow_water_equivalent':
        year = end.year if end.month > 5 else end.year-1
        result.insert(0, (f'{year}-03-01', f'{year}-05-{calendar.monthrange(year, 5)[1]}'))
    return result


def accepted_collection(root, catalog):
    catalog = (root/catalog).resolve()
    if not catalog.is_relative_to(root/'observations/work/station-migration'):
        raise ValueError('Expected staged collection')
    receipt = json.loads(catalog.with_suffix('.acceptance.json').read_text())
    checksum = digest(catalog)
    if (receipt.get('status') != 'passed' or receipt.get('catalog_sha256') != checksum
            or receipt.get('catalog') != str(catalog.relative_to(root))):
        raise ValueError('Collection acceptance/checksum mismatch')
    document = read_catalog(catalog)
    if receipt['members'] != len(document['entries']):
        raise ValueError('Collection member count mismatch')
    return catalog, document['entries'], checksum


def exercise(root, source, target, entry, end, source_catalog=None):
    started = time.monotonic()
    source_hash = digest(source)
    if source_hash != entry['sha256']:
        raise ValueError('Converted source differs from its catalog')
    target.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, target)
    if digest(target) != source_hash:
        raise ValueError('Staging copy differs')
    old = read_station(target)
    if old.signature() != entry['content_sha256']:
        raise ValueError('Converted payload differs from catalog')
    identity = {k: entry[k] for k in IDENTITY}
    if source_catalog is not None and read_member(source_catalog, **identity) != old:
        raise ValueError('Collection reader differs from direct station reader')
    catalog = target.parent/'catalog.json'
    locks = target.parent/'locks'
    write_catalog(catalog, [target], lock_root=locks)
    results = []
    for index, (start, stop) in enumerate(windows(entry, end)):
        old = read_station(target)
        incoming, receipt = acquire_window(root, target.parent/'raw'/str(index), old, start=start, end=stop)
        monthly = old.metadata['resolution'] == 'monthly' and old.metadata.get('label_kind', 'period') == 'period'
        lower, upper = (start[:7], stop[:7]) if monthly else (start, stop)
        mapping = old.metadata['mapping']
        def key(row, mapping=mapping):
            return row[mapping['series']], row[mapping['date']]
        before = {key(row): row for row in old.rows}
        result = refresh_station(root, target, incoming, start=lower, end=upper, lock_root=locks)
        new = read_station(target)
        after = {key(row): row for row in new.rows}
        for k, row in before.items():
            label = k[1][:10] if old.metadata.get('label_kind') == 'fixed_pst_timestamp' else k[1]
            if not lower <= label <= upper and after.get(k) != row:
                raise ValueError('Outside-window observation changed')
        if any(after.get(key(row)) != row for row in incoming.rows):
            raise ValueError('Published records differ from incoming response')
        unchanged_identity = target.stat().st_ino, target.stat().st_mtime_ns
        repeat = refresh_station(root, target, incoming, start=lower, end=upper, lock_root=locks)
        if repeat['status'] != 'unchanged' or unchanged_identity != (target.stat().st_ino, target.stat().st_mtime_ns):
            raise ValueError('Repeat refresh rewrote station')
        if result['status'] == 'published':
            try:
                read_catalog(catalog, verify=True)
            except ValueError:
                pass
            else:
                raise ValueError('Stale catalog was not rejected')
        write_catalog(catalog, [target], lock_root=locks)
        read_catalog(catalog, verify=True)
        if read_member(catalog, **identity) != new:
            raise ValueError('Refreshed catalog reader differs from published history')
        detail = {'start': start, 'end': stop, 'status': result['status'],
                  'incoming_rows': len(incoming.rows), 'added': len(after.keys()-before.keys()),
                  'revised': sum(k in before and before[k] != row for k, row in after.items()),
                  'returned_status_counts': dict(Counter(r[mapping['status']] for r in incoming.rows)),
                  'repeat': repeat['status'], 'catalog_verified': True,
                  'catalog_reader_verified': True}
        receipt.update(status='published_and_verified', final_signature=new.signature(), results=detail)
        atomic_json(target.parent/'raw'/str(index)/'publication.json', receipt)
        results.append(detail)
    if digest(source) != source_hash:
        raise ValueError('Original converted source changed during test')
    return {'status': 'passed', 'station': entry['station_id'], 'provider': entry['provider'],
            'sensor': entry['sensor'], 'resolution': entry['resolution'], 'windows': results,
            'original_unchanged': True, 'collection_reader_verified': source_catalog is not None,
            'seconds': time.monotonic()-started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--campaign', type=Path)
    sources.add_argument('--catalog', type=Path, help='Accepted consolidated collection catalog')
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    if args.catalog is not None:
        source_catalog, all_entries, catalog_hash = accepted_collection(root, args.catalog)
        source = source_catalog.parent
    else:
        source = (root/args.campaign).resolve()
        if not source.is_relative_to(root/'observations/work/station-migration'):
            raise ValueError('Expected staged conversion campaign')
        if json.loads((source/'acceptance.json').read_text())['status'] != 'passed':
            raise ValueError('Conversion campaign has not passed')
        source_catalog = source/'catalog.json'
        catalog_hash = digest(source_catalog)
        all_entries = read_catalog(source_catalog)['entries']
    entries = select_entries(all_entries)
    if not entries:
        raise ValueError('No histories selected')
    work = root/'observations/work/converted-station-update-test'/os.environ['SLURM_JOB_ID']
    work.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'production_modified': False,
              'source_catalog': str(source_catalog.relative_to(root)), 'source_catalog_sha256': catalog_hash,
              'collection_members': len(all_entries), 'selected_groups': len(entries),
              'stations': []}
    started = time.monotonic()
    for index, entry in enumerate(entries):
        try:
            result = exercise(root, source/entry['path'], work/str(index)/'station.nc', entry, args.end,
                              source_catalog=source_catalog)
        except Exception as error:  # noqa: BLE001 -- retain individual failure evidence and test remaining groups
            result = {'status': 'failed', 'station': entry['station_id'], 'sensor': entry['sensor'],
                      'resolution': entry['resolution'], 'error': f'{type(error).__name__}: {error}'}
        report['stations'].append(result)
        atomic_json(work/'progress.json', report)
    report['status'] = 'passed' if all(r['status'] == 'passed' for r in report['stations']) else 'failed'
    report['source_catalog_unchanged'] = digest(source_catalog) == catalog_hash
    if not report['source_catalog_unchanged']:
        report['status'] = 'failed'
    report['seconds'] = time.monotonic()-started
    atomic_json(work/'acceptance.json', report)
    print(json.dumps(report, indent=2))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
