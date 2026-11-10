"""Read-only annual coverage, accepted publication and summary lineage audit.

This checks current file identities and metadata, not a second full scientific
array scan. Scientific checks belong to the referenced publication receipts.
"""
import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

from backfill_cnrfc_forcing import identity
from backfill_forcing_summaries import audited
from netCDF4 import Dataset
from publish_prism_repair_wave import DOMAINS, PARENTS

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temporal_summary import input_path, next_month, summary_path


def source_current(rows):
    if not rows:
        raise ValueError('Empty summary input inventory')
    for row in rows:
        stat = Path(row['path']).stat()
        if (stat.st_size, stat.st_mtime_ns) != (row['bytes'], row['mtime_ns']):
            raise ValueError(f'Stale summary source: {row["path"]}')


def audit_summary(path, resolution):
    expected = {'daily': 'P1D', 'monthly': 'P1M'}[resolution]
    with Dataset(path) as ds:
        source_current(json.loads(ds.source_files))
        actual = getattr(ds, 'temporal_resolution', None)
        if actual != expected:
            raise ValueError(f'Wrong summary temporal resolution: expected {expected}, got {actual!r}')


def selected(year, stream):
    first = max(date(year, 1, 1), date(1979, 1, 2) if stream == 'retro' else date(2026, 3, 2))
    last = min(date(year, 12, 31), date(2026, 4, 18) if stream == 'retro' else date(2026, 10, 31))
    end_daily = min(date(year, 12, 31), date(2026, 4, 17) if stream == 'retro' else date(2026, 10, 30))
    result = []
    day = first
    while day <= last:
        result.append(('hourly', day))
        if day <= end_daily:
            result.append(('daily', day))
        day += timedelta(days=1)
    month = first.replace(day=1)
    while month <= end_daily:
        if month >= first and next_month(month) <= end_daily+timedelta(days=1):
            result.append(('monthly', month))
        month = next_month(month)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', type=int, required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    report = {'status': 'running', 'year': args.year, 'job_id': job, 'counts': {}, 'errors': [],
              'audit_version': '2-iso8601-summary-resolution',
              'scope': 'coverage/publication receipts/current parent identities/summary source inventory',
              'full_scientific_rescan': False}
    state = root/f'forcing/work/rain-repair-endpoints/audit/{args.year}-{job}.json'
    for stream in (('retro', 'nrt') if args.year == 2026 else ('retro',)):
        parent = root/f'forcing/outputs/conus/{stream}'
        for resolution, day in selected(args.year, stream):
            path = (input_path(parent/'hourly', day) if resolution == 'hourly' else
                    summary_path(parent/resolution, day, monthly=resolution == 'monthly'))
            try:
                if resolution == 'hourly':
                    audited(path, stream)
                else:
                    audit_summary(path, resolution)
                key = f'conus/{stream}/{resolution}'
                report['counts'][key] = report['counts'].get(key, 0)+1
            except Exception as error:  # noqa: BLE001 -- retain every per-file audit failure
                report['errors'].append({'path': str(path), 'error': str(error)})
            relative = path.relative_to(parent)
            for domain in DOMAINS:
                target = root/f'forcing/outputs/{domain}/{stream}'/relative
                source = root/f'forcing/outputs/{PARENTS[domain]}/{stream}'/relative
                try:
                    receipt = json.loads(target.with_name(target.name+'.subset.json').read_text())
                    if (receipt.get('status') != 'passed'
                            or receipt.get('source') != str(source.resolve())
                            or receipt.get('source_identity') != identity(source)
                            or receipt.get('output_identity') != identity(target)):
                        raise ValueError('Missing/stale subset lineage')
                    key = f'{domain}/{stream}/{resolution}'
                    report['counts'][key] = report['counts'].get(key, 0)+1
                except Exception as error:  # noqa: BLE001 -- retain every per-file audit failure
                    report['errors'].append({'path': str(target), 'error': str(error)})
        atomic_json(state, report)
    report['status'] = 'failed' if report['errors'] else 'passed'
    atomic_json(state, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'errors'}), flush=True)
    raise SystemExit(bool(report['errors']))


if __name__ == '__main__':
    main()
