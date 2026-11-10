#!/usr/bin/env python3
"""Subset one regenerated summary year through the four-domain hierarchy."""
import argparse
import fcntl
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path

from backfill_cnrfc_forcing import process
from publish_prism_repair_wave import DOMAINS, PARENTS

from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temporal_summary import summary_path


def task(options):
    root, relative, scratch = options
    rows = {}
    for domain in DOMAINS:
        source = root/f'forcing/outputs/{PARENTS[domain]}/retro'/relative
        output = root/f'forcing/outputs/{domain}/retro'/relative
        mask = root/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
        result = process((source, output, mask, digest(mask), scratch, list(fingerprint(source))))
        if result['status'] not in ('published','skipped'):
            raise RuntimeError(str(result))
        rows[domain] = result
    return {'relative':str(relative),'subsets':rows}


def selected_paths(parent, year, end=None):
    """Select daily summaries and only complete months inside one bounded year."""
    start = date(year, 1, 1)
    end = end or date(year, 12, 31)
    if year < 1981 or not start <= end <= date(year, 12, 31):
        raise ValueError('Summary range must lie inside one year, 1981 or later')
    paths = []
    day = start
    while day <= end:
        paths.append(summary_path(parent/'daily', day))
        day += timedelta(days=1)
    for month in range(1, 13):
        begin = date(year, month, 1)
        stop = date(year + (month == 12), month % 12 + 1, 1)
        if stop <= end + timedelta(days=1):
            paths.append(summary_path(parent/'monthly', begin, monthly=True))
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year',type=int,required=True)
    parser.add_argument('--end',type=date.fromisoformat,
                        help='Last regenerated daily date; omit incomplete monthly products')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    parent = root/'forcing/outputs/conus/retro'
    paths = selected_paths(parent, args.year, args.end)
    if not all(p.is_file() for p in paths):
        raise ValueError('Required regenerated summaries missing')
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}/summaries")
    scratch.mkdir(parents=True,exist_ok=True)
    report_path = root/'forcing/work/repaired-summary-subsets'/f"job-{os.environ['SLURM_JOB_ID']}.json"
    report = {'status':'running','year':args.year,
              'end':str(args.end or date(args.year,12,31)),'files':[]}
    with ExitStack() as locks:
        for domain in DOMAINS:
            base = root/f'forcing/outputs/{domain}'
            for name in ('.subset.lock','.subset-pilot.lock'):
                handle = locks.enter_context((base/name).open('a'))
                fcntl.flock(handle,fcntl.LOCK_SH|fcntl.LOCK_NB)
            handle = locks.enter_context((base/f'.summary-subset-{args.year}.lock').open('a'))
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            with ProcessPoolExecutor(4,mp_context=multiprocessing.get_context('spawn')) as pool:
                for result in pool.map(task, [(root,p.relative_to(parent),scratch) for p in paths]):
                    report['files'].append(result)
                    atomic_json(report_path,report)
            report['status']='passed'
        except BaseException as error:
            report.update(status='failed',error=str(error))
            raise
        finally:
            atomic_json(report_path,report)


if __name__ == '__main__':
    main()
