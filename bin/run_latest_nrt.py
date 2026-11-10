"""Locked operational extension through the latest available contiguous hour."""
import argparse
import fcntl
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
from netCDF4 import Dataset, num2date

from hydro_ops.config import load_settings
from hydro_ops.download.hrrr import HrrrDownloader
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.nrt_cycle import RecentNrt, identity, read_json
from hydro_ops.forcing.recent_observations import refresh_recent_observations
from hydro_ops.forcing.retro_publication import check_scratch


def work_directory():
    return Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')


def extension_end(first_day, today, catchup_through=None):
    if catchup_through is None:
        if (today-first_day).days > 7:
            raise ValueError('More than seven catch-up days; submit an explicit backfill first')
        return today
    if catchup_through >= today:
        raise ValueError('Explicit catch-up must end on a completed UTC day')
    if (catchup_through-first_day).days >= 7:
        raise ValueError('Explicit catch-up is limited to seven calendar days')
    return catchup_through


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-workspace', type=Path)
    parser.add_argument('--test-day', type=date.fromisoformat)
    parser.add_argument('--test-end-hour', type=int)
    parser.add_argument('--catchup-through', type=date.fromisoformat,
                        help='Explicit historical publication ceiling; at most seven calendar days per job')
    args = parser.parse_args()
    settings = load_settings()
    root = settings.project_root
    testing = args.test_workspace is not None
    if testing and args.catchup_through:
        parser.error('Explicit production catch-up cannot use an isolated test workspace')
    if testing != (args.test_day is not None and args.test_end_hour is not None):
        parser.error('Isolated tests require workspace, day and end hour together')
    if not testing and (args.test_day is not None or args.test_end_hour is not None):
        parser.error('Test bounds require an isolated workspace')
    if testing and (not args.test_workspace.resolve().is_relative_to((root/'forcing/work').resolve())
                   or args.test_workspace.resolve() == (root/'forcing/work').resolve()
                   or not 0 <= args.test_end_hour <= 23):
        parser.error('Invalid isolated workspace or end hour')
    state = root / 'forcing/status/nrt-gfs'
    state.mkdir(parents=True, exist_ok=True)
    report_state = args.test_workspace.resolve()/'status' if testing else state
    report_state.mkdir(parents=True, exist_ok=True)
    work = work_directory()
    work.mkdir(parents=True, exist_ok=True)
    check_scratch(work)
    report = {'status': 'running', 'job_id': os.environ['SLURM_JOB_ID'], 'days': [],
              'started_utc': datetime.now(UTC).isoformat(), 'scope': 'latest contiguous extension'}
    with (state / 'cycle.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            now = datetime.now(UTC)
            if testing and args.test_day > now.date():
                raise ValueError('Test day is in the future')
            overrides = ({'output_root': args.test_workspace.resolve()/'nrt/hourly',
                          'baseline_root': args.test_workspace.resolve()/'baseline/hourly'} if testing else {})
            engine = RecentNrt(root, work, now, **overrides)
            paths = sorted(engine.output.glob('*/*/*.LDASIN_DOMAIN1'))
            if not paths and not testing:
                raise ValueError('Bootstrap requires an explicit reviewed starting date')
            if paths:
                last = paths[-1]
                receipt = read_json(last.with_name(last.name+'.nrt-receipt.json'))
                if receipt.get('status') != 'passed' or receipt.get('published_identity') != identity(last):
                    raise ValueError('Latest existing NRT file lacks a current acceptance receipt')
                with Dataset(last) as data:
                    t = data['time']
                    times = num2date(t[:], t.units, only_use_cftime_datetimes=False)
                    day = date.fromisoformat(receipt['day'])
                    if not 1 <= len(times) <= 24 or [(v.date(), v.hour, v.minute, v.second) for v in times] != [(day, h, 0, 0) for h in range(len(times))]:
                        raise ValueError('Latest existing file is not a contiguous midnight prefix')
                    accepted = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=len(times)-1)
                    if len(times) == 24:
                        day += timedelta(days=1)
            else:
                day = args.test_day
                accepted = datetime.combine(day, datetime.min.time(), UTC)-timedelta(hours=1)
            if testing:
                if paths and (accepted.date() != args.test_day or accepted.hour > args.test_end_hour):
                    raise ValueError('Existing isolated output is outside requested test bounds')
                report.update(scope='isolated newest-hour acquisition test', production_modified=False,
                              canonical_source_acquisition=True, test_day=str(args.test_day),
                              test_end_hour=args.test_end_hour)
            report['latest_model_ready_hour'] = accepted.isoformat()
            downloader = HrrrDownloader(settings)
            # Bounded catch-up: fail visibly instead of silently skipping missing days.
            stop = extension_end(day, now.date(), args.catchup_through)
            if args.catchup_through:
                report.update(scope='bounded explicit catch-up', catchup_through=str(stop))
            while day <= (args.test_day if testing else stop):
                midnight = datetime.combine(day, datetime.min.time(), UTC)
                end_hour = -1
                ceiling = min(23, now.hour if day == now.date() else 23)
                if testing:
                    ceiling = min(ceiling, args.test_end_hour)
                for hour in range(-5, ceiling+1):
                    valid = midnight + timedelta(hours=hour)
                    # Canonical acquisition is serialized ahead of the revision cycle.
                    try:
                        downloader.download_hour(valid)
                    except requests.HTTPError as error:
                        if error.response.status_code != 404:
                            raise
                        if valid <= accepted:
                            raise ValueError('Required historical HRRR input unavailable') from error
                        report['awaiting_source_hour'] = valid.isoformat()
                        break
                    if hour >= 0:
                        end_hour = hour
                if end_hour < 0 or midnight+timedelta(hours=end_hour) <= accepted:
                    break
                refresh = refresh_recent_observations(root, day, end_hour)
                report.setdefault('observation_refresh', []).append(
                    {'day': day.isoformat(), 'sources': refresh})
                _atomic_json(report_state / 'latest-extension.json', report)
                result = engine.produce_day(day, end_hour=end_hour, latest=True)
                accepted = midnight+timedelta(hours=end_hour)
                report['days'].append(result)
                report['latest_model_ready_hour'] = accepted.isoformat()
                _atomic_json(report_state / 'latest-extension.json', report)
                if end_hour < 23:
                    break
                day += timedelta(days=1)
            if args.catchup_through and accepted < datetime.combine(stop, datetime.min.time(), UTC)+timedelta(hours=23):
                raise ValueError('Explicit catch-up ceiling not reached; downstream cycle must wait')
            report['status'] = 'passed'
        except Exception as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            report['finished_utc'] = datetime.now(UTC).isoformat()
            _atomic_json(report_state / 'latest-extension.json', report)
            _atomic_json(report_state / f'extension-{os.environ["SLURM_JOB_ID"]}.json', report)
            print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
