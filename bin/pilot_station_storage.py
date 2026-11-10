"""Convert four station histories in isolation and audit lossless round trips."""
import argparse
import json
import os
import time
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.observations.station_adapters import sync_archive
from hydro_ops.observations.station_catalog import read_catalog, read_member, write_catalog
from hydro_ops.observations.station_store import read_station


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extended', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    work = root/'observations/work/station-storage-pilot'/job
    work.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'job': job, 'production_modified': False, 'stations': [],
              'source': 'existing accepted processed archives; no API acquisition'}
    cohort = [('cdec', 'AMF', 'daily'), ('cdec', 'TNL', 'monthly'),
              ('cdec', 'PSH', 'daily'), ('usgs', 'USGS-02102500', 'daily')]
    if args.extended:
        cohort += [('cdec', 'AMF', 'monthly'), ('cdec', 'PSH', 'monthly'),
                   ('cdec', 'KGF', 'daily'), ('cdec', 'SCC', 'daily')]
        # Existing inventory identifies multiple series, missing values, negative
        # values, a recent start and an ended record, respectively.
        cohort += [('usgs', station, 'daily') for station in
                   ('USGS-10254970', 'USGS-09024000', 'USGS-09014050', 'USGS-09132095', 'USGS-09074000')]
    members = []
    try:
        for provider, station, resolution in cohort:
            begin = time.monotonic()
            path = work/f'{provider}-{station}-{resolution}.nc'
            kwargs = {'target': path, 'lock_root': work/'locks',
                      'cdec_inventory': Path('observations/static/stations/cdec/20261028T071314Z/stations.json')}
            result, table = sync_archive(root, provider, station, resolution, **kwargs)
            assert read_station(path) == table
            identity = path.stat().st_ino, path.stat().st_mtime_ns
            repeat, repeated_table = sync_archive(root, provider, station, resolution, **kwargs)
            assert repeated_table == table
            assert repeat['status'] == 'unchanged'
            assert identity == (path.stat().st_ino, path.stat().st_mtime_ns)
            report['stations'].append({'provider': provider, 'station': station, 'resolution': resolution,
                                       **result, 'roundtrip': 'passed', 'repeat': repeat['status'],
                                       'bytes': path.stat().st_size,
                                       'input_files': len(table.metadata['provenance']),
                                       'input_bytes': sum(p['bytes'] for p in table.metadata['provenance']),
                                       'seconds': time.monotonic()-begin})
            members.append(path)
            atomic_json(work/'acceptance.json', report)
        catalog = work/'catalog.json'
        report['catalog'] = write_catalog(catalog, members, lock_root=work/'locks')
        assert len(read_catalog(catalog, verify=True)['entries']) == len(cohort)
        before = catalog.stat().st_mtime_ns
        assert write_catalog(catalog, members, lock_root=work/'locks')['status'] == 'unchanged'
        assert before == catalog.stat().st_mtime_ns
        start = time.monotonic()
        for entry in read_catalog(catalog)['entries']:
            selected = read_member(catalog, **{k: entry[k] for k in
                                   ('provider', 'station_id', 'product', 'sensor', 'resolution')},
                                   start='2020-01' if entry['resolution'] == 'monthly' else '2020-01-01',
                                   end='2020-12' if entry['resolution'] == 'monthly' else '2020-12-31')
            assert all(r['period_label'].startswith('2020-') for r in selected.normalized())
        report['catalog'].update(verified=True, repeat='unchanged',
                                 selected_reads_seconds=time.monotonic()-start)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(work/'acceptance.json', report)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
