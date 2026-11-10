"""Serialized, journaled live refresh of the active station collection."""
import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests

from hydro_ops.download.cdec_fnf import PST
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations.station_catalog import read_catalog
from hydro_ops.observations.station_operations import (
    active_catalog,
    operation_lock,
    publish_window,
    recover_pending,
)
from hydro_ops.observations.station_store import read_station
from hydro_ops.observations.station_submit import submit
from hydro_ops.observations.station_updates import acquire_window, retain_omitted


def selected(entry, selection):
    return ((entry['provider'], entry['product']) == {
        'cdec-fnf': ('cdec', 'full_natural_flow'),
        'cdec-swe': ('cdec', 'snow_water_equivalent'),
        'usgs': ('usgs', 'discharge')}[selection])


def resume_successes(path, selection, entries):
    """Skip only successful, still-current publications from the frozen cycle."""
    report = json.loads(path.read_text())
    if report['selection'] != selection or report.get('representative'):
        raise ValueError('Resume selection mismatch')
    by_path = {e['path']:e for e in entries}
    accepted = {}
    for index,row in enumerate(report['stations']):
        if row['status'] not in ('published','unchanged'):
            continue
        sha = row.get('sha256')
        if sha is None:
            receipt = json.loads((path.parent/str(index)/'publication.json').read_text())
            if receipt['status'] != 'passed':
                raise ValueError('Resume publication not accepted')
            sha = receipt['result']['entry']['sha256']
        if row['path'] in by_path and by_path[row['path']]['sha256'] == sha:
            accepted[row['path']] = {**row,'sha256':sha,'resumed':True}
    stop = report.get('stop') or max(r['end'] for r in report['stations'] if 'end' in r)
    return accepted, datetime.fromisoformat(stop).date()


def acquire_retry(root, task, old, start, end):
    """Bound transport retries; each response retains separate acquisition evidence."""
    for attempt in range(2):
        try:
            return acquire_window(root,task/f'raw-{attempt}',old,start=start,end=end)
        except requests.RequestException:
            if attempt == 1:
                raise
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', required=True, choices=('cdec-fnf', 'cdec-swe', 'usgs'))
    parser.add_argument('--representative', action='store_true', help='One history per sensor/resolution for commissioning')
    parser.add_argument('--resume-report',type=Path)
    parser.add_argument('--continuations',type=int,choices=range(4),default=0)
    parser.add_argument('--max-hours',type=float,default=20,
                        help='Stop with durable remaining work before the 24-hour SLURM limit')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    if not 0 < args.max_hours <= 22:
        raise ValueError('Cycle budget must be between zero and 22 hours')
    started = time.monotonic()
    work = root/'observations/work/station-cycles'/os.environ['SLURM_JOB_ID']
    work.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'selection': args.selection,
              'representative': args.representative, 'stations': []}
    with operation_lock(root):
        recover_pending(root)
        catalog = active_catalog(root)
        if catalog is None:
            raise ValueError('Station collection not active')
        entries = [e for e in read_catalog(catalog)['entries'] if selected(e, args.selection)]
        if args.representative:
            groups = {}
            for entry in entries:
                groups.setdefault((entry['sensor'], entry['resolution']), entry)
            entries = list(groups.values())
        report['planned'] = len(entries)
        stop = datetime.now(PST).date()-timedelta(days=1)
        resumed = {}
        if args.resume_report:
            path = args.resume_report.resolve()
            if not path.is_relative_to(root/'observations/work/station-cycles') or args.representative:
                raise ValueError('Full station-cycle report required for resume')
            resumed,stop = resume_successes(path,args.selection,entries)
            report['resume_report'] = str(path)
        report['stop'] = str(stop)
        for index, entry in enumerate(entries):
            if entry['path'] in resumed:
                report['stations'].append(resumed[entry['path']])
                continue
            if time.monotonic()-started > args.max_hours*3600:
                report['budget_exhausted'] = True
                break
            task = work/str(index)
            try:
                old = read_station(catalog.parent/entry['path'])
                if entry['resolution'] == 'monthly':
                    end = stop.replace(day=1)-timedelta(days=1)
                    start = (end.replace(day=1)-timedelta(days=1)).replace(day=1)
                else:
                    start, end = stop-timedelta(days=30), stop
                incoming, receipt = acquire_retry(root,task,old,start.isoformat(),end.isoformat())
                monthly = entry['resolution'] == 'monthly' and old.metadata.get('label_kind', 'period') == 'period'
                lower, upper = (start.strftime('%Y-%m'), end.strftime('%Y-%m')) if monthly else (str(start), str(end))
                incoming,omitted = retain_omitted(old,incoming,start=lower,end=upper)
            except Exception as error:  # noqa: BLE001 -- isolate acquisition/validation failures, no writes yet
                report['stations'].append({'path': entry['path'], 'status': 'failed', 'error': str(error)})
                atomic_json(work/'progress.json',report)
                continue
            try:
                result = publish_window(root, entry, incoming, lower, upper, task)
            except Exception as error:  # publication uncertainty is collection-fatal
                report.update(status='failed',fatal_error=str(error),fatal_station=entry['path'])
                atomic_json(work/'acceptance.json',report)
                raise
            atomic_json(task/'publication.json', {**receipt, 'status': 'passed', 'result': result,
                        'omitted_keys_retained':omitted})
            report['stations'].append({'path': entry['path'], 'status': result['status'],
                                       'start': str(start), 'end': str(end),
                                       'sha256':result['entry']['sha256'],
                                       'omitted_keys_retained':omitted})
            atomic_json(work/'progress.json', report)
        attempted = {r['path'] for r in report['stations']}
        report['remaining'] = [e['path'] for e in entries if e['path'] not in attempted]
        report['counts'] = dict(Counter(r['status'] for r in report['stations']))
        report['failed_paths'] = [r['path'] for r in report['stations'] if r['status'] == 'failed']
        report['warning_paths'] = [r['path'] for r in report['stations'] if r.get('omitted_keys_retained')]
        report['resumed_count'] = sum(bool(r.get('resumed')) for r in report['stations'])
        report['status'] = ('partial' if report['remaining'] or report['failed_paths'] else
                            'passed_with_warnings' if report['warning_paths'] else 'passed')
        atomic_json(work/'acceptance.json', report)
        atomic_json(root/'observations/status/stations'/f'{args.selection}-latest.json',
                    {k:v for k,v in report.items() if k != 'stations'} | {'report':str(work/'acceptance.json')})
    if report['remaining'] and args.continuations:
        report['continuation'] = submit(root,args.selection,resume_report=work/'acceptance.json',
                                        continuations=args.continuations-1,after_job=os.environ['SLURM_JOB_ID'])
        atomic_json(work/'acceptance.json',report)
    print(json.dumps(report, indent=2))
    if report['status'] not in ('passed','passed_with_warnings'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
