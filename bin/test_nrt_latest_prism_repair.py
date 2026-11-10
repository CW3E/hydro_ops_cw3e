"""Isolated rain-only NRT repair with frozen, latest available PRISM releases."""
import argparse
import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from materialize_calendar_forcing import locate_hours, prism_window_metadata
from netCDF4 import Dataset

from hydro_ops.forcing.nrt_support_production import run
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint, write_day
from hydro_ops.forcing.repair_transaction import atomic_json, check_snapshot, create_snapshot


def archive(root, day):
    return root/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--days', type=int, default=2)
    parser.add_argument('--previous-boundary', type=Path,
                        help='Explicit read-only predecessor override for a missing NRT boundary')
    args = parser.parse_args()
    if not 1 <= args.days <= 31:
        parser.error('Expected 1–31 days')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    output = root/'forcing/work/nrt-latest-prism-repair'/f'job-{job}'
    output.mkdir(parents=True, exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}/nrt-rain-test")
    original = output/'originals'
    windows = output/'windows'
    days = [args.start+timedelta(days=i) for i in range(args.days)]
    window_days = [*days, days[-1]+timedelta(days=1)]
    report = {'status': 'running', 'stream': 'nrt', 'production_modified': False,
              'source_policy': 'latest_downloaded_release_frozen_at_start',
              'files': [], 'windows': [], 'prism_sources': {}}
    try:
        previous = days[0]-timedelta(days=1)
        overrides = {}
        if args.previous_boundary:
            if archive(root/'forcing/outputs/conus/nrt/hourly',previous).exists():
                raise ValueError('Cannot override an existing NRT predecessor')
            overrides[previous] = args.previous_boundary.resolve()
            report['explicit_predecessor_override'] = str(args.previous_boundary.resolve())
        create_snapshot(root/'forcing/outputs/conus/nrt/hourly', original,
                        [previous, *window_days], root, source_overrides=overrides)
        # Freeze ALL PRISM inputs before fitting any window. No silent live-file
        # replacement or unsupported historical release label is permitted.
        for day in window_days:
            source = root/'forcing/inputs/oregon_state/prism/an/4km/daily/ppt'/day.strftime(
                '%Y/%m/prism_ppt_us_25m_%Y%m%d.nc')
            release = source.with_suffix('.release.json')
            metadata = json.loads(release.read_text())
            if metadata.get('data_date') != str(day) or not metadata.get('release_date'):
                raise ValueError(f'Missing PRISM release provenance: {source}')
            before = fingerprint(source)
            target = output/'prism'/source.name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
            if fingerprint(source) != before:
                raise ValueError('PRISM changed during snapshot')
            report['prism_sources'][str(day)] = {
                'source': str(source), 'source_identity': list(before),
                'snapshot': str(target), 'sha256': digest(target), 'release': metadata}
        atomic_json(output/'acceptance.json', report)
        for day in window_days:
            info = report['prism_sources'][str(day)]
            options = argparse.Namespace(
                hours=[archive(original, day-timedelta(days=1))]*12+[archive(original, day)]*12,
                hour_indices=list(range(12,24))+list(range(12)),
                daily_output=archive(windows,day), day=day,
                diagnostics=windows/day.strftime('%Y/%m/%Y%m%d.prism_precipitation_diagnostics.nc'),
                prism=Path(info['snapshot']), prism_variable='ppt',
                weights=root/'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc',
                active_mask=root/'nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc',
                nldas_root=root/'forcing/inputs/nasa/nldas2/fora0125_hourly_v2.0',
                weak_support_threshold=.1, direct_fallback=True, bounded_inactive_fallback=False,
                compact_rain_window=True, temperature_corrected_day=None, max_iterations=200,
                work_directory=scratch/'windows'/str(day), revision='latest_available')
            options.work_directory.mkdir(parents=True, exist_ok=False)
            if run(options) != 0:
                raise ValueError(f'Window rejected: {day}')
            with Dataset(options.daily_output, 'r+') as ds:
                ds.forcing_stream = 'nrt'
                ds.prism_release_date = info['release']['release_date']
                ds.prism_source_sha256 = info['sha256']
            report['windows'].append(str(day))
            atomic_json(output/'acceptance.json', report)
        mask = root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
        with Dataset(mask) as ds:
            active, keep = np.asarray(ds['active'][:], bool), np.asarray(ds['keep'][:], bool)
        for day in days:
            paths, indices = locate_hours(windows,day)
            metadata = prism_window_metadata(paths,'nrt')
            metadata.update(archive_period=str(day), archive_granularity='utc_calendar_day',
                            calendar_day_start_utc=f'{day}T00:00:00Z',
                            calendar_day_end_utc=f'{day}T23:00:00Z',
                            prism_release_provenance=json.dumps({str(d): report['prism_sources'][str(d)]
                                for d in (day,day+timedelta(days=1))},sort_keys=True))
            report['files'].append(write_day(archive(original,day),paths,indices,
                archive(output/'hourly',day),scratch/'calendar'/str(day),day,active,keep,metadata))
            atomic_json(output/'acceptance.json', report)
        check_snapshot(original/'snapshot.json',original)
        if any(digest(Path(v['snapshot'])) != v['sha256'] for v in report['prism_sources'].values()):
            raise ValueError('Frozen PRISM input changed')
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        atomic_json(output/'acceptance.json',report)


if __name__ == '__main__':
    main()
