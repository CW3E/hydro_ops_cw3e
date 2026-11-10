"""NRT candidate adapter, isolated from the code pinned by active Retro repairs.

Snapshot of prism_support_production with optional NLDAS timing loading only.
Consolidate after the pinned Retro campaign completes; do not evolve both solvers.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.daily_archive import create_daily_archive
from hydro_ops.forcing.nrt_rain_timing import load_optional_timing
from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_rain_window import create_rain_window
from hydro_ops.forcing.prism_safeguards import (
    allocate_daily_depth,
    audit_daily_constraint,
    audit_direct_depth,
    bounded_inactive_fallback,
    direct_conservative_depth,
    fit_daily_depth,
    repair_local_fit,
    weak_support_targets,
)
from hydro_ops.forcing.prism_temperature import build_constrained_temperature_overrides


def candidate_path(path, root):
    """Prevent accidental writes to an operational stream, including via symlinks."""
    resolved = Path(path).resolve()
    scratch = None
    job, user = os.environ.get('SLURM_JOB_ID', ''), os.environ.get('SLURM_JOB_USER', '')
    if job.isdigit() and user and Path(user).name == user and user not in ('.', '..'):
        scratch = Path('/scratch')/user/f'job_{job}'
    if (not resolved.is_relative_to(Path(root).resolve() / "forcing/work")
            and not (scratch and resolved.is_relative_to(scratch.resolve()))):
        raise ValueError("support-aware-v1 is restricted to isolated forcing/work candidates")
    return resolved


def run(args):
    started = time.monotonic()
    phases = {}
    tick = started
    compact = getattr(args, 'compact_rain_window', False)
    if compact and args.temperature_corrected_day:
        raise ValueError('Compact precipitation windows cannot adjust temperature')
    root = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"]).resolve()
    threshold = getattr(args, "weak_support_threshold", 0.05)
    if not 0 < threshold <= 0.10:
        raise ValueError("Candidate weak-support threshold must be in (0, 0.10]")
    if (
        args.daily_output is None
        or args.day is None
        or args.active_mask is None
        or args.nldas_root is None
    ):
        raise ValueError(
            "Support-aware strategy requires daily output, day, active mask and NLDAS root"
        )
    destination = candidate_path(args.daily_output, root)
    candidate_path(args.diagnostics, root)
    if destination.exists():
        raise FileExistsError("Candidate overwrite refused; use a new campaign")
    indices = args.hour_indices or [0] * 24
    fields, stamps = [], []
    for path, index in zip(args.hours, indices, strict=True):
        with Dataset(path) as ds:
            fields.append(np.ma.asarray(ds["RAINRATE"][index], dtype="f8").filled(np.nan) * 3600)
            stamps.append(num2date(ds["time"][index], ds["time"].units))
    expected = [(args.day.toordinal() - 1, h) for h in range(12, 24)] + [
        (args.day.toordinal(), h) for h in range(12)
    ]
    if [(s.year, s.month, s.day, s.hour) for s in stamps] != [
        (*date.fromordinal(d).timetuple()[:3], h) for d, h in expected
    ]:
        raise ValueError("Input timestamps do not match the declared PRISM window")
    hourly = np.stack(fields)
    del fields
    shape = hourly.shape[1:]
    flat = hourly.reshape(24, -1)
    with Dataset(args.prism) as ds:
        prism = np.ma.asarray(ds[args.prism_variable][:], dtype="f8").filled(np.nan).squeeze()
    with Dataset(args.active_mask) as ds:
        active = np.asarray(ds["XLAND"][:]).squeeze() == 1
    if active.shape != shape or not np.all(np.isfinite(flat[:, active.reshape(-1)])):
        raise ValueError("Baseline active grid mismatch or missing active rainfall")
    operator = ConservativeOperator.from_cdo(args.weights)
    with Dataset(args.weights) as ds:
        fraction = np.asarray(ds["dst_grid_frac"][:])
        covered_area = np.asarray(ds["dst_grid_area"][:]) * fraction
    complete = np.all(np.isfinite(flat) & (flat >= 0), axis=0)
    missing = (
        np.bincount(
            operator.target_index[~complete[operator.source_index]], minlength=operator.target_size
        )
        > 0
    )
    eligible = np.isfinite(prism.reshape(-1)) & (fraction > 0) & ~missing
    weak = eligible & weak_support_targets(operator, fraction, active, threshold=threshold)
    hard = eligible & ~weak
    target = np.where(hard, prism.reshape(-1), np.nan)
    phases['input_and_weights'] = time.monotonic() - tick
    tick = time.monotonic()
    direct_enabled = getattr(args, 'direct_fallback', False)
    budget = 200 if direct_enabled else max(2000, args.max_iterations)
    depth, solver = fit_daily_depth(target, operator, max_iterations=budget)
    if direct_enabled:
        local = {'status': 'skipped_bounded_cost'}
    else:
        depth, local = repair_local_fit(depth, target, operator)
    if local["status"] == "local_solve_failed":
        depth, local = repair_local_fit(depth, target, operator, safety_margin=0.99)
    if local["status"] == "local_solve_failed":
        depth, local = repair_local_fit(
            depth, target, operator, safety_margin=0.99, neighborhood_rings=8
        )
    absolute = np.full(target.shape, .01)
    bounded = {"status": "disabled", "exception_indices": []}
    if getattr(args, "bounded_inactive_fallback", False):
        depth, absolute, bounded = bounded_inactive_fallback(depth, target, operator, active, local)
    estimate = operator.apply(depth)
    use_direct = direct_enabled and bool(np.any(hard & (
        ~np.isfinite(estimate) | (abs(estimate - target) > .01 + .001 * target))))
    if use_direct:
        depth, _ = direct_conservative_depth(target, operator, covered_area)
    acceptance_class = ('accepted_fallback' if use_direct else
                        'accepted_bounded_inactive' if bounded['exception_indices'] else 'accepted_strict')
    phases['spatial_fit'] = time.monotonic() - tick
    tick = time.monotonic()
    with Dataset(args.hours[0]) as ds:
        lon, lat = np.asarray(ds["lon"][:]).reshape(-1), np.asarray(ds["lat"][:]).reshape(-1)
    native_paths = [
        args.nldas_root
        / f"{s.year:04d}/NLDAS_FORA0125_H.A{s.year:04d}{s.month:02d}{s.day:02d}.020.nc"
        for s in stamps
    ]
    nldas, timing_availability = load_optional_timing(native_paths, stamps, lon, lat)
    corrected, timing = allocate_daily_depth(depth, flat, nldas)
    del nldas
    unconstrained = ~np.isfinite(depth)
    corrected[:, unconstrained] = flat[:, unconstrained]
    if not np.array_equal(np.isfinite(corrected), np.isfinite(flat)):
        raise ValueError("Support strategy changed forcing coverage")
    audit = audit_daily_constraint(corrected, target, operator, absolute_tolerance_mm=absolute)
    if use_direct:
        audit = audit_direct_depth(corrected.sum(axis=0), target, operator, covered_area)
    phases['timing_allocation_and_audit'] = time.monotonic() - tick
    tick = time.monotonic()
    report = {
        'phase_seconds': phases,
        'compact_rain_window': compact,
        "strategy": "support-aware-v1",
        "acceptance_class": acceptance_class,
        "direct_fallback": use_direct,
        "fit_iteration_budget": budget,
        "direct_audit": audit if use_direct else None,
        "day": str(args.day),
        "status": "rejected",
        "hard_cells": int(hard.sum()),
        "weak_cells": int(weak.sum()),
        "weak_support_threshold": threshold,
        "excluded_incomplete_support": int(
            (np.isfinite(prism.reshape(-1)) & (fraction > 0) & missing).sum()
        ),
        "solver": solver,
        "local_fallback": local,
        "bounded_inactive_fallback": bounded,
        "strict_target_count": 0 if use_direct else int(hard.sum()) - len(bounded["exception_indices"]),
        "direct_target_count": int(hard.sum()) if use_direct else 0,
        "bounded_target_count": len(bounded["exception_indices"]),
        "failed_cells": audit["failed_cells"],
        "timing_counts": {str(k): int((timing == k).sum()) for k in range(5)},
        "active_timing_counts": {
            str(k): int(((timing == k) & active.reshape(-1)).sum()) for k in range(5)
        },
        "scientific_validation": False,
        "baseline_sources": [str(p) for p in dict.fromkeys(args.hours)],
        "nldas_timing_sources": timing_availability["used_files"],
        "nldas_timing_availability": timing_availability,
        "weights": str(args.weights),
        "prism_source": str(args.prism),
        "timing_policy": "local baseline then nearest NLDAS timing then uniform; totals from PRISM",
        "weak_indices": np.flatnonzero(weak).tolist(),
        "coverage_preserved": True,
    }
    report_path = args.diagnostics.with_suffix(".json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    if not audit["accepted"]:
        return 2
    del flat, hourly
    overrides = {"RAINRATE": corrected.reshape((24, *shape)) / 3600}
    if args.temperature_corrected_day:
        overrides.update(
            build_constrained_temperature_overrides(
                args.hours, args.temperature_corrected_day, indices
            )
        )
    with tempfile.TemporaryDirectory(dir=args.work_directory, prefix="support-prism-") as temporary:
        staged = Path(temporary) / destination.name
        writer = create_rain_window if compact else create_daily_archive
        writer(
            args.hours,
            staged,
            args.day,
            work_directory=Path(temporary),
            source_time_indices=indices,
            time_variable_overrides=overrides,
            verification="full",
            chunk_copy=not bool(args.temperature_corrected_day),
            preserve_source_chunks=not bool(args.temperature_corrected_day),
            normalize_precipitation_timing=True,
            global_attributes={
                "prism_day": str(args.day),
                "prism_precipitation_revision": args.revision,
                "prism_precipitation_source": str(args.prism),
                "prism_reconciliation_accepted": "false",
                "prism_support_policy": "inactive_below_declared_fraction_diagnostic_only_v1",
                "prism_weak_support_threshold": threshold,
                "prism_hard_target_count": int(hard.sum()),
                "prism_weak_target_count": int(weak.sum()),
                "prism_strategy_report": str(report_path),
                "prism_bounded_inactive_target_count": len(bounded["exception_indices"]),
                "prism_acceptance_class": acceptance_class,
                "temperature_constraint_applied": "yes"
                if args.temperature_corrected_day
                else "inherited_unchanged",
            },
        )
        with Dataset(staged, "r+") as ds:
            # Keep old diagnostics explicitly historical; no false QC inheritance.
            for name in list(ds.variables):
                if name.startswith("precip_") or name == "forcing_qc_flags":
                    ds.renameVariable(name, "historical_" + name)
            v = ds.createVariable(
                "prism_timing_source_id",
                "u1",
                ("time", "y", "x"),
                zlib=True,
                complevel=2,
                chunksizes=ds['RAINRATE'].chunking(),
            )
            v.flag_values = np.arange(5, dtype="u1")
            v.flag_meanings = "unconstrained baseline nldas uniform dry"
            v[:] = timing.reshape(shape)
        with Dataset(staged) as ds:
            written = np.ma.asarray(ds["RAINRATE"][:], dtype="f8").filled(np.nan) * 3600
        final = audit_daily_constraint(written, target, operator, absolute_tolerance_mm=absolute)
        if use_direct:
            final = audit_direct_depth(written.sum(axis=0), target, operator, covered_area)
            report['direct_audit'] = final
        original_gate = audit_daily_constraint(written, target, operator)
        residual = operator.apply(written.sum(axis=0)) - prism.reshape(-1)
        phases['window_write_readback_and_audit'] = time.monotonic() - tick
        tick = time.monotonic()
        report.update(
            failed_cells=final["failed_cells"],
            original_strict_failed_cells=original_gate["failed_cells"],
            original_strict_failed_indices=np.flatnonzero(original_gate["failed_mask"]).tolist(),
            bounded_stored_residual_mm=residual[bounded["exception_indices"]].tolist(),
            weak_residual_mm=residual[weak].tolist(),
            status="passed" if final["accepted"] else "rejected",
            elapsed_seconds=time.monotonic() - started,
        )
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        if not final["accepted"]:
            return 2
        with Dataset(staged, "r+") as ds:
            ds.prism_reconciliation_accepted = "true"
            ds.prism_reconciliation_converged = "false" if use_direct else "true"
        # Existing publication expects this diagnostic acceptance artifact.
        with Dataset(args.diagnostics, "w") as ds:
            ds.accepted = "true"
            ds.strategy = "support-aware-v1"
            ds.acceptance_class = acceptance_class
            ds.report = str(report_path)
            ds.createDimension("target", operator.target_size)
            for name, values in (
                ("prism_target_mm", prism.reshape(-1)),
                ("hard_target_mm", target),
                ("absolute_tolerance_mm", absolute),
                ("residual_mm", residual),
                ("covered_fraction", fraction),
                ("covered_area", covered_area),
                ("hard_mask", hard.astype("f8")),
                ("weak_mask", weak.astype("f8")),
            ):
                ds.createVariable(name, "f8", ("target",), zlib=True)[:] = values
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        shutil.copy2(staged, partial)
        import hashlib

        with staged.open("rb") as a, partial.open("rb") as b:
            if (
                hashlib.file_digest(a, "sha256").digest()
                != hashlib.file_digest(b, "sha256").digest()
            ):
                raise ValueError("Candidate publication checksum mismatch")
        partial.replace(destination)
        phases['diagnostics_and_publication'] = time.monotonic() - tick
        report['elapsed_seconds'] = time.monotonic() - started
        report.update(status="published_candidate", output=str(destination))
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    return 0
