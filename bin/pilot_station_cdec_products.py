"""Isolated monthly FNF and native SWE station refresh pilot."""
import argparse
import os
import time
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations.station_adapters import sync_archive
from hydro_ops.observations.station_catalog import read_catalog, write_catalog
from hydro_ops.observations.station_store import read_station
from hydro_ops.observations.station_swe import sync_swe_archive
from hydro_ops.observations.station_updates import acquire_window, refresh_station


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true', help='Test hourly/event SWE histories')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    work = root/'observations/work/station-cdec-products-pilot'/os.environ['SLURM_JOB_ID']
    work.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'production_modified': False, 'stations': []}
    members = []
    cohort = ([('ADM', 3, 'hourly', '2026-10-25', '2026-10-27'),
               ('AGP', 3, 'event', '2026-10-25', '2026-10-27')] if args.native else [
                ('AMF', 65, 'monthly', '2026-09-01', '2026-10-31'),
                ('3LK', 3, 'monthly', '2026-03-01', '2026-05-31'),
                ('ADM', 82, 'daily', '2026-10-20', '2026-11-07')])
    begin = time.monotonic()
    try:
        for station, sensor, resolution, start, end in cohort:
            station_begin = time.monotonic()
            target = work/f'{station}-{sensor}-{resolution}.nc'
            if sensor == 65:
                _, old = sync_archive(root, 'cdec', station, resolution, target=target,
                    lock_root=work/'locks',
                    cdec_inventory=Path('observations/static/stations/cdec/20261028T071314Z/stations.json'))
            else:
                _, old = sync_swe_archive(root, station, sensor, resolution, target=target,
                    lock_root=work/'locks',
                    inventory=Path('observations/static/stations/cdec/swe/20261028T075431Z/stations.json'))
            assert read_station(target) == old
            incoming, receipt = acquire_window(root, work/'raw'/station, old, start=start, end=end)
            lower, upper = (start[:7], end[:7]) if sensor == 65 else (start, end)
            result = refresh_station(root, target, incoming, start=lower, end=upper, lock_root=work/'locks')
            new = read_station(target)
            mapping = old.metadata['mapping']
            def outside(rows, mapping=mapping, lower=lower, upper=upper, sensor=sensor):
                return [r for r in rows if not lower <=
                        (r[mapping['date']] if sensor == 65 else r[mapping['date']][:10]) <= upper]
            assert outside(old.rows) == outside(new.rows)
            before = target.stat().st_mtime_ns
            assert refresh_station(root, target, incoming, start=lower, end=upper,
                                   lock_root=work/'locks')['status'] == 'unchanged'
            assert before == target.stat().st_mtime_ns
            receipt.update(status='published_and_verified', final_signature=new.signature(), result=result)
            atomic_json(work/'raw'/station/'publication.json', receipt)
            report['stations'].append({'station': station, 'sensor': sensor, 'resolution': resolution,
                'before_rows': len(old.rows), 'after_rows': len(new.rows),
                'incoming_rows': len(incoming.rows), 'result': result,
                'seconds': time.monotonic()-station_begin, 'bytes': target.stat().st_size})
            atomic_json(work/'acceptance.json', report)
            members.append(target)
        write_catalog(work/'catalog.json', members, lock_root=work/'locks')
        read_catalog(work/'catalog.json', verify=True)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['seconds'] = time.monotonic()-begin
        atomic_json(work/'acceptance.json', report)
        print(report)


if __name__ == '__main__':
    main()
