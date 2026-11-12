"""Read-only keyed 1-vs-2 acquisition test; never publishes station histories."""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hydro_ops.download.usgs_discharge import daily_pages, normalize
from hydro_ops.download.usgs_request_control import ControlledRequest
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations.station_catalog import read_catalog
from hydro_ops.observations.station_operations import active_catalog
from hydro_ops.observations.station_store import read_station


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    if not os.environ.get('USGS_API_KEY'):
        raise ValueError('USGS_API_KEY required')
    work = root/'observations/work/usgs-request-benchmark'/os.environ['SLURM_JOB_ID']
    work.mkdir(parents=True, exist_ok=False)
    prior = root/'observations/work/station-cycles/5260655'
    progress = json.loads((prior/'progress.json').read_text())
    receipts = []
    for path in prior.glob('*/publication.json'):
        receipt = json.loads(path.read_text())
        if receipt.get('status') == 'passed':
            receipts.append(receipt)
    slow = sorted(receipts, key=lambda r: r.get('acquisition_seconds', 0), reverse=True)[:8]
    entries = {e['station_id']: e for e in read_catalog(active_catalog(root))['entries']
               if e['provider'] == 'usgs'}
    ids = list(dict.fromkeys([r['station_id'] for r in slow] + list(entries)[:32]))[:32]
    specs = {}
    catalog_root = active_catalog(root).parent
    # NetCDF reads are serial. Threads below perform HTTP/JSON work only.
    for station in ids:
        specs[station] = read_station(catalog_root/entries[station]['path']).metadata['auxiliary']['station.json']
    end = progress['stop']
    from datetime import date, timedelta
    start = str(date.fromisoformat(end)-timedelta(days=30))
    report = {'status': 'running', 'production_modified': False, 'key_configured': True,
              'stations': ids, 'previous_slow_stations': [r['station_id'] for r in slow],
              'start': start, 'end': end, 'interval_seconds_minimum': 5, 'variants': []}
    atomic_json(work/'report.json', report)
    reference = {}
    for workers in (1, 2):
        started = time.monotonic()
        def one(station, workers=workers):
            raw = work/f'workers-{workers}'/station
            tick = time.monotonic()
            try:
                control = ControlledRequest(root, raw/'attempts.json')
                features, sources = daily_pages(root, raw, station, start, end, request_control=control)
                records = normalize(features, specs[station], start, end)
                atomic_json(raw/'normalized.json', records)
                result = {'station': station, 'status': 'passed', 'rows': len(records),
                          'pages': len(sources), 'seconds': time.monotonic()-tick,
                          'attempts': len(control.events)}
                if workers == 1:
                    reference[station] = records
                else:
                    result['same_as_first_fetch'] = records == reference.get(station)
                return result
            except Exception as error:  # noqa: BLE001 -- diagnostic run, no live writes
                return {'station': station, 'status': 'deferred_or_failed',
                        'error_type': type(error).__name__, 'seconds': time.monotonic()-tick}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, ids))
        report['variants'].append({'workers': workers, 'seconds': time.monotonic()-started,
                                   'results': results})
        atomic_json(work/'report.json', report)
    report['status'] = ('passed' if all(r['status'] == 'passed' for v in report['variants']
                                       for r in v['results']) else 'needs_review')
    atomic_json(work/'report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'variants'}))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
