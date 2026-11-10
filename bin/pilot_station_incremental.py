"""Isolated short-window CDEC/USGS acquisition and incremental publication pilot."""
import argparse
import json
import os
import time
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations.station_adapters import sync_archive
from hydro_ops.observations.station_catalog import read_catalog, write_catalog
from hydro_ops.observations.station_store import read_station
from hydro_ops.observations.station_updates import acquire_daily_window, refresh_station


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    work = root/'observations/work/station-incremental-pilot'/os.environ['SLURM_JOB_ID']
    work.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'production_modified': False, 'stations': []}
    members = []
    started = time.monotonic()
    try:
        for provider, station in [('cdec', 'AMF'), ('usgs', 'USGS-02102500')]:
            target = work/f'{provider}-{station}.nc'
            _, previous = sync_archive(root, provider, station, 'daily', target=target, lock_root=work/'locks',
                cdec_inventory=Path('observations/static/stations/cdec/20261028T071314Z/stations.json'))
            members.append(target)
            write_catalog(work/'catalog.json', members, lock_root=work/'locks')
            incoming, receipt = acquire_daily_window(root, work/'raw'/station, previous,
                                                     start=args.start, end=args.end)
            result = refresh_station(root, target, incoming, start=args.start, end=args.end,
                                     lock_root=work/'locks')
            accepted = read_station(target)
            label = previous.metadata['mapping']['date']
            def outside(rows, label=label):
                return [r for r in rows if not args.start <= r[label] <= args.end]
            assert outside(previous.rows) == outside(accepted.rows)
            identity = target.stat().st_ino, target.stat().st_mtime_ns
            repeat = refresh_station(root, target, incoming, start=args.start, end=args.end,
                                     lock_root=work/'locks')
            assert repeat['status'] == 'unchanged'
            assert identity == (target.stat().st_ino, target.stat().st_mtime_ns)
            if result['status'] == 'published':
                try:
                    read_catalog(work/'catalog.json', verify=True)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Stale catalog was not rejected')
            write_catalog(work/'catalog.json', members, lock_root=work/'locks')
            read_catalog(work/'catalog.json', verify=True)
            receipt.update(status='published_and_verified', result=result,
                           final_signature=accepted.signature(), repeat=repeat['status'])
            atomic_json(work/'raw'/station/'publication.json', receipt)
            report['stations'].append({'provider': provider, 'station': station,
                'before_rows': len(previous.rows), 'after_rows': len(accepted.rows),
                'incoming_rows': len(incoming.rows), 'result': result, 'repeat': repeat['status']})
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['seconds'] = time.monotonic()-started
        atomic_json(work/'acceptance.json', report)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
