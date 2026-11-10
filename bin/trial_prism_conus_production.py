#!/usr/bin/env python3
"""Bounded, isolated support-aware CONUS production pilot; never promote outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from apply_static_forcing_mask import fast_clip
from materialize_calendar_forcing import locate_hours
from netCDF4 import Dataset, num2date
from produce_prism_constrained_daily import prism_window, resolve_forcing_hours

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_support_production import candidate_path

FIELDS = ("RAINRATE", "T2D", "Q2D", "PSFC", "U2D", "V2D", "SWDOWN", "LWDOWN")


def archive(root, day):
    return root / day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--weak-support-threshold", type=float, default=0.05)
    parser.add_argument("--bounded-inactive-fallback", action="store_true")
    parser.add_argument("--direct-fallback", action="store_true")
    parser.add_argument(
        "--rain-only",
        action="store_true",
        help="Read an existing retro archive; never reapply meteorological corrections",
    )
    args = parser.parse_args()
    if not 2 <= args.days <= 31:
        parser.error("Pilot requires 2–31 calendar days")
    if (args.bounded_inactive_fallback or args.direct_fallback) and not args.rain_only:
        parser.error("Bounded fallback trial currently supports rain-only mode")
    root = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    if not args.rain_only:
        candidate_path(args.baseline, root)
    job = os.environ["SLURM_JOB_ID"]
    result = root / "forcing/work/prism-conus-production" / job
    result.mkdir(parents=True, exist_ok=False)
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}/prism-pilot")
    work.mkdir(parents=True, exist_ok=False)
    mask = root / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc"
    model = root / "nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc"
    weights = root / "forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc"
    windows, output = result / "windows", result / "hourly"
    report = {
        "status": "running",
        "job_id": job,
        "production_modified": False,
        "scientific_validation": False,
        "rain_only": args.rain_only,
        "weak_support_threshold": args.weak_support_threshold,
        "bounded_inactive_fallback_enabled": args.bounded_inactive_fallback,
        "direct_fallback_enabled": args.direct_fallback,
        "phases": [],
        "files": [],
        "window_audits": [],
    }
    started = time.monotonic()

    def save():
        part = result / "acceptance.json.part"
        part.write_text(json.dumps(report, indent=2) + "\n")
        part.replace(result / "acceptance.json")

    def command(label, argv):
        tick = time.monotonic()
        if str(argv[0]) in ("bin/reconcile_prism_precipitation_day.py",
                            "bin/produce_prism_constrained_daily.py"):
            argv = [*argv, "--weak-support-threshold", args.weak_support_threshold]
            if args.bounded_inactive_fallback:
                argv = [*argv, "--bounded-inactive-fallback"]
            if args.direct_fallback:
                argv = [*argv, "--direct-fallback"]
        subprocess.run([sys.executable, *map(str, argv)], check=True)
        report["phases"].append({"phase": label, "seconds": time.monotonic() - tick})
        save()

    save()
    try:
        # Clip BEFORE closure: post-constraint clipping can invalidate PRISM totals.
        for offset in [] if args.rain_only else range(-1, args.days + 1):
            day = args.start + timedelta(days=offset)
            source, target = archive(args.baseline, day), archive(work / "baseline", day)
            target.parent.mkdir(parents=True, exist_ok=True)
            tick = time.monotonic()
            audit = fast_clip(source, mask, target, work, day.strftime("%Y%m%d"))
            with Dataset(target, "r+") as ds, Dataset(mask) as grid:
                ds.forcing_static_mask_sha256 = grid.keep_sha256
                ds.forcing_static_mask_status = "isolated_support_aware_pilot"
            report["phases"].append(
                {
                    "phase": "baseline_static_mask",
                    "day": str(day),
                    "seconds": time.monotonic() - tick,
                    "audit": audit,
                }
            )
            save()
        for offset in range(args.days + 1):
            day = args.start + timedelta(days=offset)
            if args.rain_only:
                hours, indices = resolve_forcing_hours(
                    args.baseline,
                    prism_window(day),
                    work / "unused-extracted",
                    archive_access="direct",
                    baseline_archives=None,
                )
                command(
                    f"rain-only-{day}",
                    [
                        "bin/reconcile_prism_precipitation_day.py",
                        *hours,
                        "--hour-indices",
                        *indices,
                        "--prism",
                        root
                        / "forcing/inputs/oregon_state/prism/an/4km/daily/ppt"
                        / day.strftime("%Y/%m/prism_ppt_us_25m_%Y%m%d.nc"),
                        "--weights",
                        weights,
                        "--daily-output",
                        archive(windows, day),
                        "--day",
                        day,
                        "--diagnostics",
                        windows / day.strftime("%Y/%m/%Y%m%d.prism_precipitation_diagnostics.nc"),
                        "--revision",
                        "stable",
                        "--work-directory",
                        work,
                        "--precipitation-strategy",
                        "support-aware-v1",
                        "--active-mask",
                        model,
                        "--nldas-root",
                        root / "forcing/inputs/nasa/nldas2/fora0125_hourly_v2.0",
                    ],
                )
                continue
            command(
                f"constraint-{day}",
                [
                    "bin/produce_prism_constrained_daily.py",
                    "--day",
                    day,
                    "--complete-root",
                    work / "baseline",
                    "--output-root",
                    windows,
                    "--revision",
                    "stable",
                    "--work-directory",
                    work,
                    "--allow-legacy-12utc-output",
                    "--precipitation-strategy",
                    "support-aware-v1",
                    "--active-mask",
                    model,
                    "--nldas-root",
                    root / "forcing/inputs/nasa/nldas2/fora0125_hourly_v2.0",
                ],
            )
        command(
            "calendar-assembly",
            [
                "bin/materialize_calendar_forcing.py",
                "--input-root",
                windows,
                "--output-root",
                output,
                "--start",
                args.start,
                "--days",
                args.days,
                "--hierarchical",
                "--work-directory",
                work,
                "--stream",
                "retro",
                "--require-accepted-prism-windows",
            ],
        )
        with Dataset(mask) as ds:
            active, keep = np.asarray(ds["active"][:], bool), np.asarray(ds["keep"][:], bool)
        # Check all final records, not merely successful command exits.
        for offset in range(args.days):
            day = args.start + timedelta(days=offset)
            path = archive(output, day)
            inputs, indices = locate_hours(windows, day)
            with Dataset(path) as ds:
                stamps = list(num2date(ds["time"][:], ds["time"].units))
                if [(s.year, s.month, s.day, s.hour) for s in stamps] != [
                    (day.year, day.month, day.day, h) for h in range(24)
                ]:
                    raise ValueError("Non-calendar output timestamps")
                for h, (source, index) in enumerate(zip(inputs, indices, strict=True)):
                    with Dataset(source) as window:
                        for name in FIELDS:
                            value = np.ma.asarray(ds[name][h], dtype="f8").filled(np.nan)
                            expected = np.ma.asarray(window[name][index], dtype="f8").filled(np.nan)
                            np.testing.assert_array_equal(value, expected)
                            if not np.all(np.isfinite(value[active])) or np.any(
                                np.isfinite(value[~keep])
                            ):
                                raise ValueError(f"Active/static mask violation: {day} {h} {name}")
                            if name not in ("U2D", "V2D") and np.any(value[active] < 0):
                                raise ValueError(f"Negative forcing: {name}")
            with path.open("rb") as handle:
                checksum = hashlib.file_digest(handle, "sha256").hexdigest()
            if args.rain_only:
                with Dataset(path) as repaired, Dataset(archive(args.baseline, day)) as original:
                    for name in FIELDS:
                        if name == "RAINRATE":
                            continue
                        for h in range(24):
                            a, b = repaired[name][h], original[name][h]
                            np.testing.assert_array_equal(
                                np.ma.getmaskarray(a), np.ma.getmaskarray(b)
                            )
                            np.testing.assert_array_equal(a.compressed(), b.compressed())
            report["files"].append({"path": str(path), "sha256": checksum})
            save()
        # Independently reconstruct every fully represented 12–12 window from
        # calendar files and recheck all hard targets after float32 publication.
        operator = ConservativeOperator.from_cdo(weights)
        for offset in range(1, args.days):
            day = args.start + timedelta(days=offset)
            total = np.zeros(active.shape, dtype="f8")
            for label, hours in ((day - timedelta(days=1), range(12, 24)), (day, range(12))):
                with Dataset(archive(output, label)) as ds:
                    for h in hours:
                        total += np.ma.asarray(ds["RAINRATE"][h], dtype="f8").filled(np.nan) * 3600
            diag = windows / day.strftime("%Y/%m/%Y%m%d.prism_precipitation_diagnostics.nc")
            with Dataset(diag) as ds:
                target = np.asarray(ds["hard_target_mm"][:])
                direct = getattr(ds, 'acceptance_class', '') == 'accepted_fallback'
                covered_area = np.asarray(ds['covered_area'][:]) if direct else None
                absolute = np.asarray(ds["absolute_tolerance_mm"][:]) if "absolute_tolerance_mm" in ds.variables else .01
            residual = operator.apply(total) - target
            hard = np.isfinite(target)
            failed = int(
                np.count_nonzero(
                    hard & (~np.isfinite(residual) | (abs(residual) > absolute + 0.001 * abs(target)))
                )
            )
            direct_audit = None
            if direct:
                from hydro_ops.forcing.prism_safeguards import audit_direct_depth
                direct_audit = audit_direct_depth(total, target, operator, covered_area)
                if not direct_audit['accepted']:
                    raise ValueError(f'Calendar direct-remap audit failed: {day}')
            if failed and not direct:
                raise ValueError(f"Calendar PRISM audit failed: {day}, {failed} cells")
            report["window_audits"].append(
                {"day": str(day), "declared_target_cells": int(hard.sum()), "failed_cells": 0 if direct else failed,
                 "strict_failed_cells": failed,
                 "acceptance_class": 'accepted_fallback' if direct else 'accepted_fit',
                 "direct_audit": direct_audit,
                 "strict_target_count": 0 if direct else int(np.count_nonzero(hard & (absolute == .01))),
                 "bounded_target_count": int(np.count_nonzero(hard & (absolute > .01))),
                 "original_strict_failed_indices": np.flatnonzero(
                     hard & (~np.isfinite(residual) | (abs(residual) > .01+.001*abs(target)))
                 ).tolist()}
            )
        report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        save()


if __name__ == "__main__":
    main()
