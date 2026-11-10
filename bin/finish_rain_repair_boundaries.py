"""Publish two audited endpoint repairs without refitting accepted PRISM windows."""
import argparse
import fcntl
import json
import os
from contextlib import ExitStack
from datetime import date
from pathlib import Path

import numpy as np
from materialize_calendar_forcing import locate_hours, prism_window_metadata
from netCDF4 import Dataset
from publish_nrt_latest_prism_repair import publish_one
from publish_prism_repair_wave import acquire_locks, publish_day

from hydro_ops.forcing.rain_envelope_writer import write_enveloped_day
from hydro_ops.forcing.repair_transaction import atomic_json, check_snapshot


def same_window(left, right, keep):
    """Adjacent campaigns must agree inside the final static envelope."""
    with Dataset(left) as a, Dataset(right) as b:
        for name in ('RAINRATE', 'prism_timing_source_id'):
            for hour in range(24):
                x, y = np.ma.asarray(a[name][hour])[keep], np.ma.asarray(b[name][hour])[keep]
                np.testing.assert_array_equal(np.ma.getmaskarray(x), np.ma.getmaskarray(y))
                np.testing.assert_array_equal(x.compressed(), y.compressed())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stream', choices=('retro', 'nrt'), required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    state = root/'forcing/work/rain-repair-endpoints'/args.stream
    state.mkdir(parents=True, exist_ok=True)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/endpoints')
    scratch.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'stream': args.stream, 'job_id': job}
    with Dataset(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc') as ds:
        active, keep = np.asarray(ds['active'][:], bool), np.asarray(ds['keep'][:], bool)
    try:
        with ExitStack() as stack:
            if args.stream == 'nrt':
                lock = stack.enter_context((root/'forcing/status/nrt-gfs/cycle.lock').open('a'))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                day = date(2026, 3, 2)
                acquire_locks(stack, root, day, day, blocking=True)
                path = root/'forcing/work/nrt-latest-prism-repair/job-5174437/acceptance.json'
                accepted = json.loads(path.read_text())
                if accepted['status'] != 'passed' or len(accepted['files']) != 1:
                    raise ValueError('March 2 candidate not accepted')
                task = json.loads((root/'forcing/work/nrt-latest-prism-campaign/task-0.json').read_text())
                neighbor = Path(task['candidate_report']).parent
                relative = 'windows/2026/03/20260303.LDASIN_DOMAIN1'
                same_window(path.parent/relative, neighbor/relative, keep)
                check_snapshot(path.parent/'originals/snapshot.json', path.parent/'originals')
                report['publication'] = publish_one((root, path, accepted['files'][0], state, scratch))
            else:
                day = date(2026, 4, 18)
                acquire_locks(stack, root, day, day, blocking=True)
                old = root/'forcing/work/prism-rain-pipeline/5174017'
                wave = root/'forcing/work/retro-april-boundary/job-5174017'
                check_snapshot(wave/'originals/snapshot.json', wave/'originals')
                accepted = json.loads((old/'acceptance.json').read_text())
                if accepted.get('error') != 'Invalid rainfall or coverage' or len(accepted['windows']) != 2:
                    raise ValueError('Unexpected failed boundary trial')
                for row in accepted['windows']:
                    if json.loads(Path(row['report']).read_text())['status'] != 'published_candidate':
                        raise ValueError('Unaccepted boundary window')
                relative = 'windows/2026/04/20260418.LDASIN_DOMAIN1'
                same_window(old/relative, root/'forcing/work/prism-rain-pipeline/5174006_63'/relative, keep)
                # Persist the accepted candidate pointer, allowing publication retries.
                pointer = state/'candidate.json'
                if pointer.exists():
                    candidate_job = json.loads(pointer.read_text())['job_id']
                else:
                    candidate_job = job
                    output = root/f'forcing/work/prism-rain-pipeline/{job}_0'
                    output.mkdir(parents=True, exist_ok=False)
                    paths, indices = locate_hours(old/'windows', day)
                    metadata = prism_window_metadata(paths, 'retro')
                    metadata.update(archive_period=str(day), archive_granularity='utc_calendar_day',
                                    calendar_day_start_utc=f'{day}T00:00:00Z',
                                    calendar_day_end_utc=f'{day}T23:00:00Z')
                    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                    row = write_enveloped_day(wave/'originals'/relative, paths, indices,
                                              output/'hourly'/relative, scratch/'assembly',
                                              day, active, keep, metadata)
                    atomic_json(output/'acceptance.json', {
                        'status': 'passed', 'files': [row], 'reused_windows': str(old),
                        'adjacent_window_equal_inside_envelope': True})
                    atomic_json(pointer, {'job_id': job})
                report['publication'] = publish_day((root, wave, state, scratch,
                                                     candidate_job, date(2026, 4, 1), day))
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(state/f'acceptance-{job}.json', report)


if __name__ == '__main__':
    main()
