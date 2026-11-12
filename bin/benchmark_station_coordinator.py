"""Cached CDEC coordinator comparison; no network or production publication."""
import argparse
import json
import multiprocessing
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations import station_operations as ops
from hydro_ops.observations.station_catalog import read_catalog
from hydro_ops.observations.station_conversion import digest
from hydro_ops.observations.station_preparation import PreparedPublisher, prepare_window
from hydro_ops.observations.station_scheduling import pop_completed
from hydro_ops.observations.station_store import read_station, read_station_header


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-benchmark', default='5260658')
    args = parser.parse_args()
    if not args.source_benchmark.isdecimal():
        parser.error('Numeric source benchmark required')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    prior = root/'observations/work/station-preparation-benchmark'/args.source_benchmark
    accepted = json.loads((prior/'report.json').read_text())
    if accepted['status'] != 'passed' or accepted['selection'] != 'cdec-swe':
        raise ValueError('Accepted cached SWE benchmark required')
    inputs = accepted['inputs']
    for item in inputs:
        if digest(Path(item['source'])) != item['entry']['sha256']:
            raise ValueError('Source history changed')
    work = root/'observations/work/station-coordinator-benchmark'/os.environ['SLURM_JOB_ID']
    work.mkdir(parents=True, exist_ok=False)
    source_doc = read_catalog(prior/'reference'/ops.COLLECTION/'catalog.json')
    selected = {item['entry']['path']: item['entry'] for item in inputs}
    source_doc['entries'] = [selected.get(e['path'], e) for e in source_doc['entries']]
    report = {'status': 'running', 'production_modified': False, 'network_requests': 0,
              'source_benchmark': args.source_benchmark, 'stations': len(inputs),
              'workers': 4, 'variants': [],
              'caveat': 'Fixed variant order may favor later filesystem caches; acquisition is cached.'}
    expected = {}
    for label in ('fifo-full-context', 'ready-full-context', 'ready-header-context'):
        mirror = work/label
        base = mirror/ops.COLLECTION
        for item in inputs:
            target = base/item['entry']['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item['source'], target)
        atomic_json(base/'catalog.json', source_doc)
        atomic_json(mirror/ops.STATE/'active.json', {'status': 'active', 'catalog': ops.COLLECTION+'/catalog.json'})
        variant = {'name': label, 'context_seconds': 0., 'completion_order': [], 'timings': []}
        report['variants'].append(variant)
        tick = time.monotonic()
        with ops.operation_lock(mirror), ProcessPoolExecutor(
                max_workers=4, mp_context=multiprocessing.get_context('spawn')) as pool:
            publisher = PreparedPublisher(mirror)
            pending = []
            def finish(label=label, pending=pending, publisher=publisher, variant=variant):
                future, index = pending.pop(0) if label.startswith('fifo') else pop_completed(pending)
                prepared = future.result()
                mark = time.monotonic()
                publisher.publish(prepared)
                variant['timings'].append(prepared['timings'] | {'publication_seconds': time.monotonic()-mark})
                variant['completion_order'].append(index)
                atomic_json(work/'report.json', report)
            for index, item in enumerate(inputs):
                mark = time.monotonic()
                context = (read_station_header if label == 'ready-header-context' else read_station)(
                    base/item['entry']['path'])
                incoming = read_station(item['incoming'])
                if context.metadata != incoming.metadata or context.fields != incoming.fields:
                    raise ValueError('Acquisition header differs from full-history context')
                variant['context_seconds'] += time.monotonic()-mark
                del context, incoming
                pending.append((pool.submit(prepare_window, mirror, item['entry'], item['incoming'],
                                            item['start'], item['end'],
                                            mirror/f'observations/work/station-cycles/test/{index}'), index))
                if len(pending) >= 4:
                    finish()
            while pending:
                finish()
        variant['elapsed_seconds'] = time.monotonic()-tick
        current = {e['path']: e for e in read_catalog(base/'catalog.json')['entries']}
        for entry in source_doc['entries']:
            if entry['path'] not in selected and current[entry['path']] != entry:
                raise ValueError('Unrelated catalog changed')
        for item in inputs:
            name = item['entry']['path']
            signature = read_station(base/name).signature()
            if label == 'fifo-full-context':
                expected[name] = signature
            if signature != expected[name] or digest(base/name) != current[name]['sha256']:
                raise ValueError('Optimized result differs from reference')
        variant['equivalence'] = 'passed_all_source_columns_metadata_flags_and_values'
        atomic_json(work/'report.json', report)
    report['status'] = 'passed'
    atomic_json(work/'report.json', report)


if __name__ == '__main__':
    main()
