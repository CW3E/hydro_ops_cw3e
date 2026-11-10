#!/usr/bin/env python3
"""Sandbox-only subset worker scaling and verified-copy versus hard-link timing."""
import multiprocessing
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from backfill_cnrfc_forcing import process

from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json, sandbox, sync_directory


def subset_day(options):
    root, directory, scratch = map(Path, options[:3])
    stamp = options[3]
    relative = f'{stamp[:4]}/{stamp[4:6]}/{stamp}.LDASIN_DOMAIN1'
    parents = {'cnrfc': 'conus', 'b120': 'cnrfc', 'ucrb': 'conus', 'yampa': 'ucrb'}
    started = time.monotonic()
    rows = {}
    for domain, parent in parents.items():
        source = (root / 'forcing/outputs/conus/retro/hourly' / relative if parent == 'conus'
                  else directory / parent / relative)
        target = directory / domain / relative
        mask = root / f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
        result = process((source, target, mask, digest(mask), scratch, list(fingerprint(source))))
        if result['status'] != 'published':
            raise RuntimeError(str(result))
        rows[domain] = result
    return {'day': stamp, 'seconds': time.monotonic()-started, 'subsets': rows}


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    output = sandbox(root / 'forcing/work/prism-publication-benchmark' / job, root)
    output.mkdir(parents=True, exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}/publication-benchmark")
    scratch.mkdir(parents=True, exist_ok=False)
    labels = ['19810903', '19810904', '19810905', '19810906']
    sources = [root / f'forcing/outputs/conus/retro/hourly/1981/09/{d}.LDASIN_DOMAIN1'
               for d in labels]
    identities = [fingerprint(p) for p in sources]
    report = {'status': 'running', 'production_modified': False, 'scaling': [],
              'scope': 'sandbox subsets retain full audits; copy/link microbenchmark only'}
    try:
        # Each method validates identical accepted bytes. Linking is not enabled
        # in production by this experiment and requires immutable candidate policy.
        source = sources[0]
        checksum = digest(source)
        report['publication_io'] = []
        for method in ('copy', 'hardlink'):
            start = time.monotonic()
            staging = output / f'{method}.part'
            target = output / f'{method}.LDASIN_DOMAIN1'
            if method == 'copy':
                shutil.copyfile(source, staging)
            else:
                os.link(source, staging)
            transfer = time.monotonic()-start
            if digest(staging) != checksum:
                raise ValueError('Checksum mismatch')
            with staging.open('rb') as handle:
                os.fsync(handle.fileno())
            os.replace(staging, target)
            sync_directory(target.parent)
            report['publication_io'].append({'method': method, 'transfer_seconds': transfer,
                                             'total_seconds': time.monotonic()-start})
        for workers in (1, 2, 4):
            directory = output / f'workers-{workers}'
            tasks = [(str(root), str(directory), str(scratch), label) for label in labels]
            start = time.monotonic()
            with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context('spawn')) as pool:
                rows = list(pool.map(subset_day, tasks))
            report['scaling'].append({'workers': workers, 'seconds': time.monotonic()-start,
                                      'days': rows})
            atomic_json(output / 'acceptance.json', report)
        if [fingerprint(p) for p in sources] != identities:
            raise ValueError('Production input identity changed')
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(output / 'acceptance.json', report)


if __name__ == '__main__':
    main()
