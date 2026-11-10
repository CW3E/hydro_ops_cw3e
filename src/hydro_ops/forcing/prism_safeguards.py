"""Experimental PRISM safeguards; not yet enabled in production runners.

Timing allocation is deliberately separate from the spatial total solver. A
fallback must never change the prescribed total or hide a failed spatial fit.
"""

from __future__ import annotations

import numpy as np

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator


def direct_conservative_depth(target, operator, covered_area):
    """Reverse overlap areas, not normalized weights, in one finite pass.

    Conservation is over the common covered area, not entire boundary cells.
    covered_area is PRISM cell area times its NWM-covered fraction.
    """
    target = np.asarray(target, dtype=float).reshape(-1)
    area = np.asarray(covered_area, dtype=float).reshape(-1)
    if target.size != operator.target_size or area.size != target.size:
        raise ValueError('Grid mismatch')
    required = np.isfinite(target)
    if not required.any() or np.any(target[required] < 0):
        raise ValueError('Invalid precipitation targets')
    if np.any(~np.isfinite(area[required]) | (area[required] <= 0)):
        raise ValueError('Invalid covered areas')
    rows = np.bincount(operator.target_index, weights=operator.weight,
                       minlength=operator.target_size)
    if np.any(rows[required] <= 0):
        raise ValueError('Missing overlap support')
    links = required[operator.target_index]
    ti, si = operator.target_index[links], operator.source_index[links]
    overlap = operator.weight[links] / rows[ti] * area[ti]
    source_area = np.bincount(si, weights=overlap, minlength=operator.source_size)
    volume = np.bincount(si, weights=overlap * target[ti], minlength=operator.source_size)
    depth = np.divide(volume, source_area, out=np.full(operator.source_size, np.nan),
                      where=source_area > 0)
    return depth, source_area


def audit_direct_depth(total, target, operator, covered_area):
    """Audit stored hourly allocation against independent direct-remap totals."""
    expected, area = direct_conservative_depth(target, operator, covered_area)
    actual = np.asarray(total, dtype=float).reshape(-1)
    if actual.size != expected.size:
        raise ValueError('Grid mismatch')
    used = area > 0
    failed = used & (~np.isfinite(actual) | (actual < 0)
                     | (abs(actual - expected) > 1e-5 + 1e-6 * expected))
    wanted_volume = float(np.sum(np.asarray(target)[np.isfinite(target)]
                                 * np.asarray(covered_area)[np.isfinite(target)]))
    actual_volume = float(np.sum(actual[used] * area[used]))
    volume_ok = bool(np.isfinite(actual_volume) and
                     abs(actual_volume - wanted_volume) <= 1e-6 * max(wanted_volume, 1e-12))
    return {'accepted': bool(not failed.any() and volume_ok),
            'failed_cells': int(failed.sum()), 'volume_passed': volume_ok,
            'reference_volume_mm_area': wanted_volume,
            'stored_volume_mm_area': actual_volume,
            'relative_volume_error': (actual_volume - wanted_volume) / max(wanted_volume, 1e-12)}


def weak_support_targets(operator, covered_fraction, active_source, *, threshold=.05):
    """Classify tiny, wholly inactive intersections; never change source masks.

    Unknown support remains hard-constrained. Caller must also require complete
    domain/link support before using this classification for acceptance.
    """
    fraction = np.asarray(covered_fraction, dtype=float).reshape(-1)
    active = np.asarray(active_source, dtype=bool).reshape(-1)
    if fraction.size != operator.target_size or active.size != operator.source_size:
        raise ValueError('Support grid mismatch')
    if not 0 < threshold < 1:
        raise ValueError('Invalid coverage threshold')
    active_links = np.bincount(operator.target_index[active[operator.source_index]],
                               minlength=operator.target_size)
    return np.isfinite(fraction) & (fraction > 0) & (fraction < threshold) & (active_links == 0)


def repair_local_fit(depth, prism_depth, operator, *, max_source_cells=10000,
                     neighborhood_rings=1, safety_margin=.5, absolute_tolerance_mm=.01):
    """Experimental local LP minimizing absolute change, with fixed exterior.

    Every finite target touched by adjustable sources is constrained. Failure
    means this bounded neighborhood was not repaired, NOT global infeasibility.
    A half-tolerance margin protects the subsequent float32 hourly round trip.
    """
    from scipy.optimize import linprog
    from scipy.sparse import csr_matrix, eye, hstack, vstack

    initial = np.asarray(depth, dtype=float).reshape(-1)
    if neighborhood_rings < 1 or not 0 < safety_margin < 1:
        raise ValueError('Invalid neighborhood or safety margin')
    target = np.asarray(prism_depth, dtype=float).reshape(-1)
    absolute = np.broadcast_to(np.asarray(absolute_tolerance_mm, dtype=float), target.shape)
    if np.any(~np.isfinite(absolute)) or np.any(absolute < 0):
        raise ValueError('Invalid absolute tolerance')
    if initial.size != operator.source_size or target.size != operator.target_size:
        raise ValueError('Grid mismatch')
    required = np.isfinite(target)
    if np.any(target[required] < 0) or np.any(initial[np.isfinite(initial)] < 0):
        raise ValueError('Negative precipitation')
    estimate = operator.apply(initial)
    failed = required & (~np.isfinite(estimate) | (np.abs(estimate-target) > absolute+.001*target))
    report = {'failed_before': int(failed.sum()), 'applied': False}
    if not failed.any():
        return initial.copy(), dict(report, status='not_needed')
    # Include all sources of immediate neighboring targets, then constrain every
    # target influenced by those sources; other source depths stay fixed.
    sources = np.unique(operator.source_index[failed[operator.target_index]])
    for _ in range(neighborhood_rings):
        touched = np.unique(operator.target_index[np.isin(operator.source_index, sources)])
        sources = np.unique(operator.source_index[np.isin(operator.target_index, touched)
                                                 & required[operator.target_index]])
    rows = np.unique(operator.target_index[np.isin(operator.source_index, sources)
                                          & required[operator.target_index]])
    report.update(source_cells=int(sources.size), target_cells=int(rows.size),
                  failed_target_indices=np.flatnonzero(failed).tolist(),
                  neighborhood_rings=neighborhood_rings, safety_margin=safety_margin)
    if not sources.size or sources.size > max_source_cells:
        return initial.copy(), dict(report, status='unsupported_neighborhood')
    linked = np.isin(operator.target_index, rows)
    if not np.all(np.isfinite(initial[operator.source_index[linked]])):
        return initial.copy(), dict(report, status='missing_support')
    matrix = csr_matrix((operator.weight, (operator.target_index, operator.source_index)),
                        shape=(operator.target_size, operator.source_size))[rows]
    sums = np.asarray(matrix.sum(axis=1)).ravel()
    matrix = matrix.multiply((1 / sums)[:, None]).tocsr()
    local = matrix[:, sources]
    original = initial[sources]
    fixed = matrix @ np.nan_to_num(initial) - local @ original
    wanted = target[rows] - fixed
    tolerance = safety_margin * (absolute[rows] + .001 * target[rows])
    n = len(sources)
    zero = csr_matrix(local.shape)
    identity = eye(n, format='csr')
    constraints = vstack((hstack((local, zero)), hstack((-local, zero)),
                          hstack((identity, -identity)), hstack((-identity, -identity))),
                         format='csr')
    bounds = np.concatenate((wanted+tolerance, -wanted+tolerance, original, -original))
    result = linprog(np.r_[np.zeros(n), np.ones(n)], A_ub=constraints, b_ub=bounds,
                     bounds=(0, None), method='highs', options={'time_limit': 60})
    report.update(solver_status=int(result.status), solver_message=result.message)
    if not result.success:
        # Diagnostic minimax solve: smallest multiplier of the ORIGINAL gate,
        # not permission to accept that multiplier or publish this candidate.
        full_tolerance = absolute[rows] + .001 * target[rows]
        slack = csr_matrix(-full_tolerance[:, None])
        minimax = linprog(np.r_[np.zeros(n), 1.],
                          A_ub=vstack((hstack((local, slack)), hstack((-local, slack)))),
                          b_ub=np.r_[wanted, -wanted], bounds=(0, None), method='highs',
                          options={'time_limit': 60})
        if minimax.success:
            report['minimum_tolerance_multiplier'] = float(minimax.x[-1])
        report['neighbor_targets'] = [
            {'index': int(r), 'target_mm': float(target[r]), 'estimate_mm': float(estimate[r])}
            for r in rows
        ]
        return initial.copy(), dict(report, status='local_solve_failed')
    candidate = initial.copy()
    candidate[sources] = result.x[:n]
    after = operator.apply(candidate)
    bad = required & (~np.isfinite(after) | (np.abs(after-target) > absolute+.001*target))
    report.update(failed_after=int(bad.sum()), max_change_mm=float(np.max(np.abs(
        candidate[sources]-original))))
    if bad.any():
        return initial.copy(), dict(report, status='global_audit_failed')
    return candidate, dict(report, status='repaired', applied=True)


def bounded_inactive_fallback(depth, target, operator, active_source, strict_report,
                              *, maximum_cells=32):
    """Candidate-only 0.05-mm allowance at failed wholly inactive targets.

    Requires a demonstrated local infeasibility under the original tolerance.
    No target is removed. Every target with any active contributor remains at
    0.01 mm + 0.1%; exceptions use 0.05 mm ABSOLUTE, with no relative addition.
    The independent stored-value audit must use the returned tolerance vector.
    """
    target = np.asarray(target, dtype=float).reshape(-1)
    absolute = np.full(target.shape, .01)
    unchanged = np.asarray(depth).copy()
    report = {'status': 'not_applied', 'exception_indices': [], 'maximum_cells': maximum_cells,
              'maximum_absolute_residual_mm': .05}
    if (strict_report.get('status') != 'local_solve_failed'
            or strict_report.get('minimum_tolerance_multiplier', 0) <= 1):
        return unchanged, absolute, report
    active = np.asarray(active_source, dtype=bool).reshape(-1)
    active_count = np.bincount(operator.target_index[active[operator.source_index]],
                               minlength=operator.target_size)
    estimate = operator.apply(depth)
    failed = np.isfinite(target) & (np.abs(estimate-target) > .01+.001*target)
    # Only enlarge a tolerance when the original is tighter than the fixed cap.
    selected = failed & (active_count == 0) & (.01+.001*target < .05)
    indices = np.flatnonzero(selected)
    if not len(indices) or len(indices) > maximum_cells:
        return unchanged, absolute, dict(report, status='unsupported_exception_scope')
    absolute[selected] = .05-.001*target[selected]
    candidate, local = repair_local_fit(depth, target, operator, safety_margin=.99,
                                        neighborhood_rings=8, absolute_tolerance_mm=absolute)
    if not local['applied']:
        return unchanged, np.full(target.shape, .01), dict(report, status='bounded_solve_failed', solve=local)
    report.update(status='applied', exception_indices=indices.tolist(), solve=local,
                  original_residual_mm=(estimate-target)[selected].tolist(),
                  repaired_residual_mm=(operator.apply(candidate)-target)[selected].tolist())
    return candidate, absolute, report


def fit_daily_depth(
    prism_depth: np.ndarray, operator: ConservativeOperator, *, max_iterations: int = 2000,
) -> tuple[np.ndarray, dict]:
    """Experimental positive spatial fit, seeded from PRISM rather than dry baseline.

    Multiplicative sparse updates have no baseline-relative cap. This is NOT an
    acceptance decision: the independently reconstructed hours must be audited.
    Incompatible overlapping-grid targets can fail to converge.
    """
    from scipy.sparse import csr_matrix

    target = np.asarray(prism_depth, dtype=float).reshape(-1)
    if target.size != operator.target_size or np.any(target[np.isfinite(target)] < 0):
        raise ValueError("Invalid PRISM target")
    if max_iterations < 1:
        raise ValueError("Expected positive iteration limit")
    required = np.isfinite(target)
    links = required[operator.target_index]
    matrix = csr_matrix(
        (operator.weight[links], (operator.target_index[links], operator.source_index[links])),
        shape=(operator.target_size, operator.source_size),
    )
    row_sum = np.asarray(matrix.sum(axis=1)).ravel()
    matrix = matrix.multiply(np.divide(1, row_sum, out=np.zeros_like(row_sum),
                                       where=row_sum > 0)[:, None]).tocsr()
    transpose = matrix.T.tocsr()
    columns = np.asarray(matrix.sum(axis=0)).ravel()
    desired = np.where(required, target, 0)
    depth = np.divide(transpose @ desired, columns, out=np.zeros(operator.source_size),
                      where=columns > 0)
    for iteration in range(1, max_iterations + 1):
        estimate = matrix @ depth
        error = np.abs(estimate - desired)
        if np.all(error[required] <= .005 + .0005 * desired[required]):
            break
        ratio = np.divide(desired, estimate, out=np.zeros_like(desired), where=estimate > 0)
        depth *= np.divide(transpose @ ratio, columns, out=np.zeros_like(depth),
                           where=columns > 0)
    estimate = matrix @ depth
    depth[columns == 0] = np.nan
    return depth, {"iterations": iteration, "max_residual_mm": float(
        np.max(np.abs(estimate[required] - target[required]))
    ) if required.any() else None}


def allocate_daily_depth(
    daily_depth: np.ndarray,
    baseline: np.ndarray,
    nldas: np.ndarray,
    *,
    minimum_wet_depth: float = 0.1,
    maximum_amplification: float = 10.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Allocate prescribed depths using local baseline, NLDAS, then uniform timing.

    All inputs are depths in mm, on the SAME grid and SAME 24-hour PRISM window.
    Provenance: 0=unconstrained/missing, 1=baseline, 2=NLDAS, 3=uniform,
    4=dry target. Incomplete/negative profiles are unusable. The amplification
    threshold selects timing; it does NOT cap the prescribed precipitation.
    """
    total = np.asarray(daily_depth, dtype=float)
    baseline, nldas = np.asarray(baseline, dtype=float), np.asarray(nldas, dtype=float)
    if baseline.shape != (24, *total.shape) or nldas.shape != baseline.shape:
        raise ValueError("Expected matching 24-hour profiles and daily grid")
    if minimum_wet_depth <= 0 or maximum_amplification < 1:
        raise ValueError("Invalid timing thresholds")
    if np.any(total[np.isfinite(total)] < 0):
        raise ValueError("Negative prescribed precipitation")
    result = np.full_like(baseline, np.nan)
    source = np.zeros(total.shape, dtype=np.uint8)
    dry = np.isfinite(total) & (total == 0)
    result[:, dry], source[dry] = 0, 4
    pending = np.isfinite(total) & (total > 0)
    for code, profile in ((1, baseline), (2, nldas)):
        depth = np.sum(profile, axis=0)
        usable = pending & np.all(np.isfinite(profile) & (profile >= 0), axis=0)
        usable &= depth >= minimum_wet_depth
        if code == 1:
            usable &= total <= maximum_amplification * depth
        result[:, usable] = profile[:, usable] / depth[usable] * total[usable]
        source[usable] = code
        pending &= ~usable
    result[:, pending] = total[pending] / 24
    source[pending] = 3
    return result, source


def audit_daily_constraint(
    hourly_depth: np.ndarray,
    prism_depth: np.ndarray,
    operator: ConservativeOperator,
    *,
    absolute_tolerance_mm: float = 0.01,
    relative_tolerance: float = 0.001,
) -> dict:
    """Fail closed on EVERY finite PRISM target, including absent mapping/support.

    Input must be the reconstructed/published hourly depths, not solver flags.
    Deliberate out-of-domain targets must be explicitly masked to NaN by caller.
    Partial hourly profiles remain missing rather than silently summing to zero.
    """
    hourly = np.asarray(hourly_depth, dtype=float)
    target = np.asarray(prism_depth, dtype=float).reshape(-1)
    if hourly.shape[0] != 24 or hourly.size != 24 * operator.source_size:
        raise ValueError("Expected 24 hourly fields on the operator source grid")
    if target.size != operator.target_size:
        raise ValueError("Target grid mismatch")
    absolute = np.broadcast_to(np.asarray(absolute_tolerance_mm, dtype=float), target.shape)
    if np.any(~np.isfinite(absolute)) or np.any(absolute < 0) or relative_tolerance < 0:
        raise ValueError("Negative acceptance tolerance")
    flat = hourly.reshape(24, -1)
    valid_source = np.all(np.isfinite(flat) & (flat >= 0), axis=0)
    depth = np.where(valid_source, np.sum(flat, axis=0), np.nan)
    estimate = operator.apply(depth)
    # apply() ignores missing links: explicitly reject partial spatial support.
    missing_links = np.bincount(
        operator.target_index[~valid_source[operator.source_index]],
        minlength=operator.target_size,
    ) > 0
    required = np.isfinite(target)
    residual = estimate - target
    failed = required & (
        (target < 0) | missing_links | ~np.isfinite(estimate)
        | (np.abs(residual) > absolute + relative_tolerance * target)
    )
    return {
        "accepted": bool(np.any(required) and not np.any(failed)),
        "required_cells": int(required.sum()),
        "failed_cells": int(failed.sum()),
        "failed_mask": failed.reshape(np.asarray(prism_depth).shape),
        "residual_mm": residual.reshape(np.asarray(prism_depth).shape),
    }
