#!/usr/bin/env python3
"""Submit one coordinated NWM forcing cycle with persistent run provenance."""

from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.forcing.nrt_cycle import activation, configuration, require_operational_gfs
from hydro_ops.forcing.streams import forcing_stream_root


def latest_time_limit() -> str:
    """Two-hour routine ceiling, with an explicit catch-up override."""
    value = os.environ.get("HYDRO_OPS_NRT_LATEST_MINUTES", "120")
    if not value.isdecimal() or not 1 <= int(value) <= 10080:
        raise ValueError("HYDRO_OPS_NRT_LATEST_MINUTES must be 1–10080 whole minutes")
    hours, minutes = divmod(int(value), 60)
    return f"{hours:02d}:{minutes:02d}:00"


def bounded_replay(settings, args, stream):
    """Explicit canary branch: never launch broad catch-up or source refresh."""
    from publish_bounded_prism_replay import bounded_plan
    plan = bounded_plan(settings.project_root, args.replay_workspace, stream, args.replay_day)
    command = ['sbatch', '--parsable', f'--partition={settings.slurm_partition}',
               f'--job-name=nwm-cycle-{args.cycle}-bounded-replay-{args.replay_day:%Y%m%d}',
               'slurm/publish_bounded_prism_replay.sh', '--workspace', str(args.replay_workspace.resolve()),
               '--stream', stream, '--day', str(args.replay_day), '--publish']
    if settings.slurm_account:
        command.insert(1, f'--account={settings.slurm_account}')
    if getattr(args, 'replay_after', None):
        command.insert(1, f'--dependency=afterok:{args.replay_after}')
    report = {'status': 'planned', 'cycle': args.cycle, 'day': str(args.replay_day),
              'stream': stream, 'scope': plan['scope'], 'command': command,
              'source_refresh': False, 'broad_revision_cycle': False}
    if not args.dry_run:
        job = subprocess.check_output(command, cwd=settings.project_root, text=True,
                                      env={k: v for k, v in os.environ.items()
                                           if k != 'SBATCH_RESERVATION'}).strip().split(';')[0]
        report.update(status='submitted', job_id=job)
        (settings.work_root/f'bounded-cycle-{job}.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return 0


def cycle_window(cycle: str, today: date) -> tuple[str, date, date, int, int]:
    if cycle == "six-hourly":
        end = today - timedelta(days=2)
        return "nrt", end - timedelta(days=9), end, 14, 8
    if cycle == "daily":
        end = today - timedelta(days=2)
        return "nrt", end - timedelta(days=199), end, 200, 12
    end = today - timedelta(days=183)
    return "retro", end - timedelta(days=44), end, 45, 8


def retro_backlog(root: Path, window_start: date, floor: date = date(1981, 1, 1)) -> list[date]:
    """Find filename gaps behind the rolling window; scientific audits run in SLURM.

    Daily PRISM starts in 1981. The 1979–1980 monthly-constrained archive is
    intentionally outside this workflow. Fail closed on an uninitialized root.
    """
    if not root.is_dir():
        raise ValueError('Retro archive missing; initialize with an explicit window')
    available = {p.name[:8] for p in root.glob('*/*/????????.LDASIN_DOMAIN1') if p.is_file()}
    if not available:
        raise ValueError('Retro archive empty; initialize with an explicit window')
    gaps = []
    day = floor
    while day < window_start:
        if day.strftime('%Y%m%d') not in available:
            gaps.append(day)
        day += timedelta(days=1)
    return gaps


def main() -> int:
    launched_at = datetime.now(UTC).isoformat()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", required=True, choices=("six-hourly", "daily", "monthly-retro"))
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument(
        "--dependency",
        help="optional SLURM dependency for the initial explicit-window baseline array",
    )
    parser.add_argument(
        "--skip-source-refresh",
        action="store_true",
        help="use the existing source archive (for explicit historical retro windows)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument('--skip-model-runs', action='store_true',
                        help='Suppress downstream model submissions for this cycle and its continuation')
    parser.add_argument('--replay-workspace', type=Path, help='Accepted isolated replay for a bounded canary')
    parser.add_argument('--replay-day', type=date.fromisoformat)
    parser.add_argument('--replay-after', help='Successful job dependency for this bounded canary only')
    parser.add_argument("--revisions-only", action="store_true",
                        help="Internal daily continuation after latest-hour publication")
    args = parser.parse_args()
    if bool(args.replay_workspace) != bool(args.replay_day):
        parser.error('Supply both --replay-workspace and --replay-day')
    if args.replay_after and (not args.replay_workspace or not args.replay_after.isdecimal()):
        parser.error('--replay-after requires a bounded replay and numeric job ID')
    if args.replay_workspace and (args.cycle == 'six-hourly' or args.start or args.end
                                 or args.skip_source_refresh or args.dependency or args.revisions_only):
        parser.error('Bounded replay requires daily/monthly-retro without other window options')
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be supplied together")
    if args.start and (args.cycle != "monthly-retro" or args.end < args.start):
        parser.error("explicit windows require monthly-retro and an ordered date range")
    if args.skip_source_refresh and not args.start:
        parser.error("--skip-source-refresh requires an explicit historical window")
    if args.dependency and not args.start:
        parser.error("--dependency requires an explicit historical window")
    settings = load_settings()
    stream, start, end, source_lookback, prism_concurrency = cycle_window(
        args.cycle, datetime.now(UTC).date()
    )
    explicit_window = args.start is not None
    if explicit_window:
        start, end = args.start, args.end
    settings.work_root.mkdir(parents=True, exist_ok=True)
    lock_path = settings.work_root / "update_nwm_forcing.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another coordinator submission is active", file=sys.stderr)
            return 1
        active = subprocess.run(
            ["squeue", "--noheader", "--user", getpass.getuser(), "--format=%A|%j"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        coordinator_prefix = f"nwm-cycle-{args.cycle}"
        stream_markers = (
            ("nwm-cycle-six-hourly", "nwm-cycle-daily", "prism-nrt-calendar-batches")
            if stream == "nrt"
            else ("nwm-cycle-monthly-retro", "prism-retro-calendar-batches")
        )
        if not explicit_window and any(
            any(marker in line for marker in stream_markers) for line in active
        ):
            print(f"SKIP active coordinated {stream} workflow ({coordinator_prefix})")
            return 0
        if args.replay_workspace:
            return bounded_replay(settings, args, stream)
        nominal_start = start
        backlog = []
        if args.cycle == 'monthly-retro' and not explicit_window:
            backlog = retro_backlog(forcing_stream_root(settings.project_root, 'retro'), start)
            if backlog:
                start = min(start, backlog[0])
        plan = {
            "created": launched_at,
            "cycle": args.cycle,
            "stream": stream,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "source_lookback_days": source_lookback,
            "prism_concurrency": prism_concurrency,
            "partition": settings.slurm_partition,
            "account": settings.slurm_account,
            "status": "planned" if args.dry_run else "submitting",
            "maximum_attempts": 4,
            "initial_dependency": args.dependency,
            "skip_model_runs": args.skip_model_runs,
        }
        if args.cycle in ('daily', 'monthly-retro'):
            plan['routine_revision_replay'] = {
                'stream': stream, 'maximum_dates': 2,
                'after': 'normal publication and summaries',
                'incomplete_context': 'defer and retain queue obligation',
                'cron_activation': False}
        if args.cycle == 'monthly-retro':
            plan['nominal_window_start'] = nominal_start.isoformat()
            plan['backlog_scan_enabled'] = not explicit_window
            plan['historical_filename_gaps'] = [day.isoformat() for day in backlog]
            plan['coverage_scan_scope'] = '1981 onward; filename existence, not scientific acceptance'
        if stream == "nrt":
            require_operational_gfs(settings.project_root)
            config = configuration(settings.project_root)
            plan["recent_nrt_gfs_requested"] = bool(config.get("enabled"))
            plan["recent_nrt_gfs_active"] = activation(settings.project_root)
            if plan["recent_nrt_gfs_active"]:
                recent_start = max(start, end - timedelta(days=config["lookback_days"] - 1))
                plan["recent_nrt_start"] = recent_start.isoformat()
                plan["recent_nrt_end"] = end.isoformat()
        if stream == 'nrt' and not args.revisions_only:
            plan['publication_ceiling'] = 'latest_available_contiguous_hour_at_execution'
            plan['daily_revision_continuation'] = args.cycle == 'daily'
            plan['window_dates_scope'] = 'revision workflow only; not latest-hour publication cutoff'
        print(json.dumps(plan, indent=2))
        if stream == "nrt" and not args.revisions_only:
            # Latest-hour publication has no dependency on optional-source refreshes.
            if any(any(f'|{source}_download' in line for source in ('hrrr', 'mrms', 'stage4'))
                   for line in active):
                print('SKIP active source acquisition; avoid concurrent canonical writers')
                return 0
            worker_partition = os.environ.get('HYDRO_OPS_NRT_PARTITION', 'compute-128')
            plan['latest_worker_partition'] = worker_partition
            latest_limit = latest_time_limit()
            plan['latest_time_limit'] = latest_limit
            command = ['sbatch', '--parsable', f'--partition={worker_partition}',
                       '--nodes=1', '--ntasks=1', '--cpus-per-task=128', '--tmp=240000',
                       f'--time={latest_limit}', f'--job-name=nwm-cycle-{args.cycle}-latest-hour',
                       f'--output={settings.log_root}/nrt-latest-extension-%j.out',
                       '--wrap', 'source bin/project_environment.sh; exec ' +
                       shlex.join([sys.executable, 'bin/run_latest_nrt.py'])]
            if settings.slurm_account:
                command.insert(1, f'--account={settings.slurm_account}')
            print(shlex.join(command))
            if args.dry_run:
                return 0
            job = subprocess.check_output(command, cwd=settings.project_root, text=True).strip().split(';')[0]
            plan.update(status='latest_extension_submitted', latest_extension_job_id=job)
            manifest = settings.work_root / f'nwm-latest-cycle-{args.cycle}-{job}.json'
            manifest.write_text(json.dumps(plan, indent=2)+'\n')
            if args.cycle == 'daily':
                follow = ['sbatch', '--parsable', f'--partition={settings.slurm_partition}',
                          '--nodes=1', '--ntasks=1', '--cpus-per-task=1', '--time=00:30:00',
                          f'--dependency=afterok:{job}', '--job-name=nrt-daily-revision-launch',
                          f'--output={settings.log_root}/nrt-daily-revision-launch-%j.out',
                          '--wrap', 'source bin/project_environment.sh; exec ' + shlex.join([
                              sys.executable, 'bin/update_nwm_forcing.py', '--cycle', 'daily', '--revisions-only',
                              *(['--skip-model-runs'] if args.skip_model_runs else [])])]
                if settings.slurm_account:
                    follow.insert(1, f'--account={settings.slurm_account}')
                plan['revision_launcher_job_id'] = subprocess.check_output(
                    follow, cwd=settings.project_root, text=True).strip().split(';')[0]
            from hydro_ops.forcing.cnrfc_sync import submit as submit_cnrfc
            try:
                # Daily gets a full catch-up after revision/summary completion;
                # six-hourly also catches any summaries published since its last run.
                plan['cnrfc_sync_frequency'] = 'all' if args.cycle == 'six-hourly' else 'hourly'
                plan['cnrfc_hourly_job_id'] = submit_cnrfc(
                    settings.project_root, stream='nrt', frequency=plan['cnrfc_sync_frequency'], after=job,
                    partition=settings.slurm_partition, account=settings.slurm_account)
            except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
                plan['cnrfc_submission_error'] = str(error)
            manifest.write_text(json.dumps(plan, indent=2)+'\n')
            print(json.dumps(plan, indent=2))
            return 2 if plan.get('cnrfc_submission_error') else 0
        if args.dry_run:
            return 0
        refresh_output = ""
        if not args.skip_source_refresh:
            refresh = subprocess.run(
                [
                    sys.executable,
                    "bin/update_forcing.py",
                    "--repair-lookback-days",
                    str(source_lookback),
                ],
                cwd=settings.project_root,
                check=False,
                capture_output=True,
                text=True,
            )
            print(refresh.stdout, end="", flush=True)
            print(refresh.stderr, end="", file=sys.stderr, flush=True)
            if refresh.returncode:
                return refresh.returncode
            refresh_output = refresh.stdout
        source_names = {
            "nldas2_download",
            "stage4_download",
            "prism_download",
            "hrrr_download",
            "mrms_download",
        }
        active_source_ids = [
            job_id
            for line in active
            if "|" in line
            for job_id, job_name in [line.split("|", 1)]
            if job_name in source_names
        ]
        source_ids = list(
            dict.fromkeys(
                [*active_source_ids, *re.findall(r"Submitted batch job (\d+)", refresh_output)]
            )
        )
        if args.skip_source_refresh:
            source_ids = []
        plan["source_job_ids"] = source_ids
        if args.cycle == 'monthly-retro' and not args.skip_source_refresh:
            # Source updates also invalidate older NRT outputs. Persist those
            # obligations independently of this cycle's Retro production gate.
            queue_command = ['sbatch', '--parsable', '--job-name=prism-release-revision-queue',
                             f'--partition={settings.slurm_partition}']
            if settings.slurm_account:
                queue_command.append(f'--account={settings.slurm_account}')
            if source_ids:
                queue_command.append('--dependency=afterany:'+':'.join(source_ids))
            queue_command.append('slurm/queue_prism_revisions.sh')
            plan['prism_revision_queue_job_id'] = subprocess.check_output(
                queue_command, cwd=settings.project_root, text=True,
                env={k: v for k, v in os.environ.items() if k != 'SBATCH_RESERVATION'}).strip().split(';')[0]
            plan['prism_revision_replay'] = 'bounded_after_normal_publication'
        baseline_start, baseline_end = start - timedelta(days=1), end + timedelta(days=1)
        if plan.get("recent_nrt_gfs_active"):
            baseline_end = recent_start  # Halo for the older PRISM window only.
        baseline_command = [
            sys.executable,
            "bin/submit_forcing_days.py",
            "--start",
            baseline_start.isoformat(),
            "--end",
            baseline_end.isoformat(),
            "--missing-only",
            "--max-concurrent",
            "16",
            "--cpus-per-task",
            "12",
            "--tmp-mb",
            "120000",
            "--job-name",
            f"nwm-cycle-{args.cycle}-baseline-{start:%Y%m%d}-{end:%Y%m%d}",
        ]
        no_retro_baseline_work = False
        if stream == "retro":
            missing_targets: list[date] = []
            day = start
            retro_root = forcing_stream_root(settings.project_root, "retro")
            while day <= end:
                output = retro_root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
                if not output.is_file():
                    missing_targets.append(day)
                day += timedelta(days=1)
            dependencies = sorted(
                {
                    target + timedelta(days=offset)
                    for target in missing_targets
                    for offset in (-1, 0, 1)
                }
            )
            if dependencies:
                baseline_command.extend(
                    ["--only-days", *(item.isoformat() for item in dependencies)]
                )
            else:
                no_retro_baseline_work = True
        if args.dependency:
            baseline_command.extend(["--dependency", args.dependency])
        elif source_ids:
            baseline_command.extend(["--dependency", f"afterany:{':'.join(source_ids)}"])
        baseline_output = "eligible_days=0\n" if no_retro_baseline_work else ""
        if not no_retro_baseline_work:
            baseline = subprocess.run(
                baseline_command,
                cwd=settings.project_root,
                check=False,
                capture_output=True,
                text=True,
            )
            print(baseline.stdout, end="", flush=True)
            print(baseline.stderr, end="", file=sys.stderr, flush=True)
            if baseline.returncode:
                return baseline.returncode
            baseline_output = baseline.stdout
        match = re.search(r"Submitted batch job (\d+)", baseline_output)
        baseline_id = match.group(1) if match else None
        plan["baseline_job_id"] = baseline_id
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        manifest = settings.work_root / f"nwm-forcing-cycle-{args.cycle}-{stamp}.json"
        plan["manifest"] = str(manifest.resolve())
        exports = (
            "ALL,"
            f"HYDRO_OPS_PROJECT_ROOT={settings.project_root},"
            f"HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_CYCLE_MANIFEST={manifest.resolve()}"
        )
        command = [
            "sbatch",
            f"--partition={settings.slurm_partition}",
            f"--job-name=nwm-cycle-{args.cycle}-continue-{start:%Y%m%d}-{end:%Y%m%d}",
            "--cpus-per-task=1",
            "--time=48:00:00",
            "--requeue",
            f"--export={exports}",
            f"--output={settings.log_root}/nwm-cycle-{args.cycle}-%j.out",
            "slurm/converge_nwm_forcing_cycle.py",
        ]
        if settings.slurm_account:
            command.insert(2, f"--account={settings.slurm_account}")
        dependencies = [*source_ids, *([baseline_id] if baseline_id else [])]
        if dependencies:
            command.insert(2, f"--dependency=afterany:{':'.join(dependencies)}")
        plan["status"] = "submitted"
        manifest.write_text(json.dumps(plan, indent=2) + "\n")
        continued = subprocess.run(command, cwd=settings.project_root, check=False)
        return continued.returncode


if __name__ == "__main__":
    raise SystemExit(main())
