"""Replay cached provider updates against isolated histories; never publish live data."""
import argparse
import csv
import gzip
import io
import json
import multiprocessing
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from datetime import date
from pathlib import Path

from hydro_ops.download.usgs_discharge import normalize
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations import station_operations as ops
from hydro_ops.observations.station_catalog import read_catalog
from hydro_ops.observations.station_conversion import digest
from hydro_ops.observations.station_preparation import PreparedPublisher, prepare_window
from hydro_ops.observations.station_store import StationTable, read_station, write_station
from hydro_ops.observations.station_updates import retain_omitted


def incoming_from_cache(old, task, start, end):
    raw = next((p for p in sorted(task.glob('raw*')) if (p/'acquisition.json').exists()), None)
    if raw is None:
        raise ValueError('No accepted cached acquisition')
    receipt = json.loads((raw/'acquisition.json').read_text())
    if receipt['start'] != start or receipt['end'] != end:
        raise ValueError('Cached acquisition dates differ')
    import hashlib
    if old.metadata['provider'] == 'cdec':
        from hydro_ops.download import cdec_fnf, cdec_swe
        payload = gzip.decompress((raw/'response.json.gz').read_bytes())
        source = receipt['sources'][0]
        if hashlib.sha256(payload).hexdigest() != source['sha256']:
            raise ValueError('Cached response checksum mismatch')
        spec = {'station_id': old.metadata['station_id'], 'resolution': old.metadata['resolution'],
                'start': start, 'request_start': start, 'request_end': end,
                'sensor_number': int(old.metadata['sensor']),
                'duration_code': {'daily': 'D', 'monthly': 'M', 'hourly': 'H', 'event': 'E'}[old.metadata['resolution']]}
        values = json.loads(payload)
        rows = (cdec_swe.normalize(values, spec) if old.metadata['product'] == 'snow_water_equivalent'
                else cdec_fnf.normalize(values, spec, date.fromisoformat(end)))
    else:
        features = []
        for path in sorted(raw.glob('page-*.json.gz')):
            payload = gzip.decompress(path.read_bytes())
            source = json.loads(path.with_name(path.name+'.source.json').read_text())
            if hashlib.sha256(payload).hexdigest() != source['sha256']:
                raise ValueError('Cached response checksum mismatch')
            features.extend(json.loads(payload)['features'])
        rows = normalize(features, old.metadata['auxiliary']['station.json'], start, end)
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=old.fields)
    writer.writeheader()
    writer.writerows(rows)
    buffer.seek(0)
    return StationTable(deepcopy(old.metadata), old.fields.copy(), list(csv.DictReader(buffer)))


def select_cached(choices, sources, count, stratified=False):
    choices = sorted(choices, key=lambda item: sources[item[1]['path']]['rows'])
    if not stratified:
        if len(choices) < count:
            raise ValueError('Not enough completed fresh updates for benchmark')
        return [choices[i*(len(choices)-1)//(count-1)] for i in range(count)]
    groups = {}
    for item in choices:
        e = sources[item[1]['path']]
        groups.setdefault((e['sensor'], e['resolution']), []).append(item)
    selected = {}
    for group in groups.values():
        for item in (group[0], group[len(group)//2], group[-1]):
            selected[item[1]['path']] = item
    if len(selected) > count:
        raise ValueError('Station budget too small for small/median/largest in each sensor/resolution')
    return list(selected.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-job', default='5249499')
    parser.add_argument('--stations', type=int, default=32)
    parser.add_argument('--selection', choices=('usgs', 'cdec-swe', 'cdec-fnf'), default='usgs')
    parser.add_argument('--max-workers', type=int, choices=(4, 8), default=8)
    args = parser.parse_args()
    if not args.source_job.isdecimal() or not 8 <= args.stations <= 64:
        parser.error('Numeric job and 8–64 stations required')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    work = root/'observations/work/station-preparation-benchmark'/os.environ['SLURM_JOB_ID']
    work.mkdir(parents=True, exist_ok=False)
    active = json.loads((root/ops.STATE/'active.json').read_text())
    catalog = root/active['source_catalog']
    if digest(catalog) != active['source_catalog_sha256']:
        raise ValueError('Migration source catalog changed')
    source_doc = read_catalog(catalog)
    sources = {str(ops.permanent_name(e)): e for e in source_doc['entries']}
    history = root/'observations/work/station-cycles'/args.source_job
    progress_path = history/'acceptance.json'
    if not progress_path.exists():
        progress_path = history/'progress.json'
    progress = json.loads(progress_path.read_text())
    if progress['selection'] != args.selection:
        raise ValueError('Cached provider selection mismatch')
    choices = [(row.get('task_index', i), row) for i, row in enumerate(progress['stations'])
               if row['status'] in (('published',) if args.selection == 'usgs' else ('published', 'unchanged'))
               and not row.get('resumed')
               and row['path'] in sources]
    selected = select_cached(choices, sources, args.stations, stratified=args.selection != 'usgs')
    inputs = []
    for index, row in selected:
        entry = sources[row['path']]
        origin = catalog.parent/entry['path']
        if digest(origin) != entry['sha256']:
            raise ValueError('Migration station source changed')
        old = read_station(origin)
        incoming = incoming_from_cache(old, history/str(index), row['start'], row['end'])
        path = work/'inputs'/f'{index}.nc'
        write_station(path, incoming, lock_root=work/'inputs/locks')
        monthly = entry['resolution'] == 'monthly' and old.metadata.get('label_kind', 'period') == 'period'
        inputs.append({'source': str(origin), 'entry': entry | {'path': row['path']},
                       'incoming': str(path), 'start': row['start'][:7] if monthly else row['start'],
                       'end': row['end'][:7] if monthly else row['end']})
    report = {'status': 'running', 'job': os.environ['SLURM_JOB_ID'],
              'stations': len(inputs), 'selection': args.selection, 'variants': [], 'production_modified': False,
              'network_requests': 0, 'inputs': inputs,
              'caveat': 'Cached responses; filesystem caches may favor later variants. Not an end-to-end API benchmark.'}
    atomic_json(work/'report.json', report)
    expected = {}
    for workers in (0, 1, 4, 8):
        if workers > args.max_workers:
            continue
        label = 'reference' if not workers else f'prepared-{workers}'
        mirror = work/label
        entries = [e | {'path': str(ops.permanent_name(e))} for e in source_doc['entries']]
        base = mirror/ops.COLLECTION
        for item in inputs:
            target = base/item['entry']['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item['source'], target)
        # Full-size catalog retains realistic per-publication metadata overhead;
        # only the selected station payloads exist in this isolated mirror.
        atomic_json(base/'catalog.json', source_doc | {'entries': entries})
        atomic_json(mirror/ops.STATE/'active.json', {'status': 'active', 'catalog': ops.COLLECTION+'/catalog.json'})
        variant = {'name': label, 'workers': workers or 1, 'timings': [], 'completed': 0}
        report['variants'].append(variant)
        started = time.monotonic()
        with ops.operation_lock(mirror):
            if not workers:
                for i, item in enumerate(inputs):
                    mark = time.monotonic()
                    old = read_station(base/item['entry']['path'])
                    incoming = read_station(item['incoming'])
                    variant_timing = {'read_seconds': time.monotonic()-mark}
                    mark = time.monotonic()
                    incoming, _ = retain_omitted(old, incoming, start=item['start'], end=item['end'])
                    variant_timing['merge_seconds'] = time.monotonic()-mark
                    mark = time.monotonic()
                    ops.publish_window(mirror, item['entry'], incoming, item['start'], item['end'],
                                       mirror/f'observations/work/station-cycles/benchmark/{i}')
                    variant_timing['publication_seconds'] = time.monotonic()-mark
                    variant['timings'].append(variant_timing)
                    variant['completed'] += 1
                    atomic_json(work/'report.json', report)
            else:
                publisher = PreparedPublisher(mirror)
                with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
                    futures = [pool.submit(prepare_window, mirror, item['entry'], item['incoming'],
                               item['start'], item['end'],
                               mirror/f'observations/work/station-cycles/benchmark/{i}')
                               for i, item in enumerate(inputs)]
                    for future in as_completed(futures):
                        prepared = future.result()
                        mark = time.monotonic()
                        publisher.publish(prepared)
                        variant['timings'].append(prepared['timings'] | {
                            'publication_seconds': time.monotonic()-mark})
                        variant['completed'] += 1
                        atomic_json(work/'report.json', report)
        variant['elapsed_seconds'] = time.monotonic()-started
        current = {e['path']: e for e in read_catalog(base/'catalog.json')['entries']}
        selected_paths = {item['entry']['path'] for item in inputs}
        for entry in entries:
            if entry['path'] not in selected_paths and current[entry['path']] != entry:
                raise ValueError('Unrelated catalog entry changed')
        for item in inputs:
            name = item['entry']['path']
            table = read_station(base/name)
            if not workers:
                expected[name] = table.signature()
            if table.signature() != expected[name] or digest(base/name) != current[name]['sha256']:
                raise ValueError('Prepared output differs from serial reference')
        variant['equivalence'] = 'passed_all_source_columns_metadata_flags_and_values'
        atomic_json(work/'report.json', report)
        print(json.dumps({k: v for k, v in variant.items() if k != 'timings'}), flush=True)
    report['status'] = 'passed'
    atomic_json(work/'report.json', report)


if __name__ == '__main__':
    main()
