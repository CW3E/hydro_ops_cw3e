"""Bounded metadata-only summary repair and receipt-gated stale subset refresh."""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import h5py
import numpy as np
from backfill_cnrfc_forcing import discover, process
from backfill_forcing_summaries import audited, publication_locks
from netCDF4 import Dataset
from publish_prism_repair_wave import DOMAINS, PARENTS

from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.rain_summary_publication import publish_summary
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temporal_summary import migration_stable_identity


def relocated(row, parent):
    path = Path(row['path'])
    # Only the documented insertion of hourly/ is permitted. No basename search.
    relative = path.relative_to(parent)
    if len(relative.parts) == 3 and relative.parts[0].isdigit() and relative.parts[1].isdigit():
        path = parent/'hourly'/relative
    return path


def unchanged_arrays(original, candidate):
    """Compare every dataset, including rainfall, without inflating compressed fields."""
    with h5py.File(original) as a, h5py.File(candidate) as b:
        if set(a) != set(b):
            raise ValueError('Dataset inventory changed')
        for name in a:
            x, y = a[name], b[name]
            if (x.shape, x.dtype, x.chunks) != (y.shape, y.dtype, y.chunks):
                raise ValueError(f'Dataset encoding changed: {name}')
            if x.chunks:
                if x.id.get_num_chunks() != y.id.get_num_chunks():
                    raise ValueError('Chunk inventory changed')
                for i in range(x.id.get_num_chunks()):
                    offset = x.id.get_chunk_info(i).chunk_offset
                    if x.id.read_direct_chunk(offset) != y.id.read_direct_chunk(offset):
                        raise ValueError(f'Compressed payload changed: {name}')
            elif x.ndim:
                for i in range(0, x.shape[0], 32):
                    np.testing.assert_array_equal(x[i:i+32], y[i:i+32])
            else:
                np.testing.assert_array_equal(x[()], y[()])


def repair_metadata(task):
    path, parent, work = task
    receipt = work/'transactions'/f'{path.name}.json'
    candidate = work/'candidates'/path.name
    validation_path = work/'validation'/f'{path.name}.json'
    if validation_path.exists():
        proof = json.loads(validation_path.read_text())
        return publish_summary(candidate, path, receipt, proof['inputs'], proof)
    original_id = fingerprint(path)
    with Dataset(path) as ds:
        rows = json.loads(ds.source_files)
        metadata = {key: ds.getncattr(key) for key in ds.ncattrs()}
    revised, pins = [], {}
    for row in rows:
        source = relocated(row, parent)
        ident = fingerprint(source)
        if (ident[1], ident[2]) != (row['bytes'], row['mtime_ns']):
            # A monthly inventory may reference a daily summary just rewritten
            # above. Require its exact old identity and unchanged-array proof.
            tx = json.loads((work/'transactions'/f'{source.name}.json').read_text())
            if (tx['status'] != 'published' or tx['identity'] != list(ident)
                    or tx['old_identity'][1:] != [row['bytes'], row['mtime_ns']]
                    or tx['validation'].get('all_arrays_unchanged') is not True):
                raise ValueError(f'Unexplained source change: {source}')
        if source.parent.parent.parent == parent/'hourly':
            audited(source)
        revised.append({'path': str(source.resolve()), 'bytes': ident[1], 'mtime_ns': ident[2]})
        pins[str(source)] = list(ident)
    if revised == rows:
        return {'status': 'unchanged', 'target': str(path)}
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, candidate)
    signature = hashlib.sha256(json.dumps({
        'algorithm': metadata['aggregation_algorithm'],
        'inputs': [migration_stable_identity(r) for r in revised],
        'start': metadata['interval_start_utc'][:10],
        'stop': metadata['interval_end_utc'][:10],
        'from_daily': metadata['input_resolution'] == 'daily',
        'spec': json.loads(metadata['reducer_spec']),
        'domain': metadata['domain'], 'stream': metadata['forcing_stream'],
    }, sort_keys=True).encode()).hexdigest()
    with Dataset(candidate, 'r+') as ds:
        ds.source_files = json.dumps(revised)
        ds.aggregation_signature = signature
    unchanged_arrays(path, candidate)
    if fingerprint(path) != original_id:
        raise ValueError('Summary changed during metadata repair')
    with Dataset(candidate) as ds:
        if json.loads(ds.source_files) != revised:
            raise ValueError('Metadata readback failed')
    proof = {'status': 'passed', 'all_arrays_unchanged': True, 'inputs': pins,
             'sha256': digest(candidate), 'old_source_files': rows,
             'new_source_files': revised, 'scope': 'provenance-only'}
    atomic_json(validation_path, proof)
    return publish_summary(candidate, path, receipt, pins, proof)


def main():
    from datetime import date
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=('1979', '1980', 'nrt'), required=True)
    parser.add_argument('--canary', action='store_true', help='Only January 2, 1979 metadata and subsets')
    args = parser.parse_args()
    if args.canary and args.scope != '1979':
        parser.error('Canary requires --scope 1979')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    work = root/'forcing/work/rain-repair-endpoints/lineage-repair'/args.scope
    work.mkdir(parents=True, exist_ok=True)
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/lineage')
    scratch.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'scope': args.scope, 'job_id': job, 'canary': args.canary,
              'metadata': [], 'subsets': {}}
    stream = 'nrt' if args.scope == 'nrt' else 'retro'
    try:
        with ExitStack() as stack:
            lock = stack.enter_context((work/'repair.lock').open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if stream == 'nrt':
                lock = stack.enter_context((root/'forcing/status/nrt-gfs/cycle.lock').open('a'))
                fcntl.flock(lock, fcntl.LOCK_EX)
            else:
                year = int(args.scope)
                parent = root/'forcing/outputs/conus/retro'
                stack.enter_context(publication_locks(parent, date(year, 1, 1), date(year, 12, 31), True))
                for resolution in (('daily',) if args.canary else ('daily', 'monthly')):
                    paths = [p for p in discover(parent.parent, ('retro',), (resolution,))
                             if p.name.startswith('19790102' if args.canary else args.scope)]
                    with ProcessPoolExecutor(max_workers=4) as pool:
                        for row in pool.map(repair_metadata, [(p, parent, work) for p in paths]):
                            report['metadata'].append(row)
                            atomic_json(work/f'acceptance-{job}.json', report)
            # Ordered exclusive locks serialize parent/child refresh across years.
            for domain in DOMAINS:
                for name in ('.subset-pilot.lock', '.subset.lock'):
                    lock = stack.enter_context((root/f'forcing/outputs/{domain}'/name).open('a'))
                    fcntl.flock(lock, fcntl.LOCK_EX)
            for domain in DOMAINS:
                parent = root/f'forcing/outputs/{PARENTS[domain]}/{stream}'
                target = root/f'forcing/outputs/{domain}/{stream}'
                paths = [p for p in discover(parent.parent, (stream,))
                         if p.name.startswith('20261031' if stream == 'nrt' else
                                              '19790102' if args.canary else args.scope)]
                mask = root/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
                mask_hash = digest(mask)
                tasks = [(p, target/p.relative_to(parent), mask, mask_hash, scratch,
                          list(fingerprint(p))) for p in paths]
                with ProcessPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(process, tasks))
                report['subsets'][domain] = results
                atomic_json(work/f'acceptance-{job}.json', report)
                if any(r['status'] not in ('published', 'skipped') for r in results):
                    raise ValueError(f'Subset refresh failed: {domain}')
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(work/f'acceptance-{job}.json', report)


if __name__ == '__main__':
    main()
