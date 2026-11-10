import numpy as np

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_safeguards import (
    audit_daily_constraint,
    bounded_inactive_fallback,
    repair_local_fit,
)


def case(target=(.297, .002)):
    op = ConservativeOperator(2, 2, np.array([0, 0, 1]), np.array([0, 1, 1]),
                              np.array([1., .0433, .9567]))
    depth = np.array([.2862, 0.])
    target = np.array(target)
    _, report = repair_local_fit(depth, target, op, safety_margin=.99)
    return op, depth, target, report


def test_bounded_inactive_preserves_active_gate_and_float32_audit():
    op, depth, target, strict = case()
    assert strict['minimum_tolerance_multiplier'] > 1
    result, absolute, report = bounded_inactive_fallback(depth, target, op, [False, True], strict)
    assert report['status'] == 'applied'
    assert report['exception_indices'] == [0]
    assert absolute[1] == .01
    assert abs((absolute+.001*target)[0]-.05) < 1e-12
    stored = np.broadcast_to(result/24/3600, (24, 2)).astype('f4').astype('f8')*3600
    assert audit_daily_constraint(stored, target, op, absolute_tolerance_mm=absolute)['accepted']
    original = audit_daily_constraint(stored, target, op)
    assert original['failed_mask'].tolist() == [True, False]


def test_no_active_target_relaxation():
    op, depth, target, strict = case()
    result, absolute, report = bounded_inactive_fallback(depth, target, op, [True, True], strict)
    assert report['status'] != 'applied'
    np.testing.assert_array_equal(result, depth)
    np.testing.assert_array_equal(absolute, [.01, .01])


def test_gross_conflict_and_budget_fail_closed():
    op, depth, target, strict = case((20., 0.))
    result, absolute, report = bounded_inactive_fallback(depth, target, op, [False, True], strict)
    assert report['status'] == 'bounded_solve_failed'
    np.testing.assert_array_equal(result, depth)
    np.testing.assert_array_equal(absolute, [.01, .01])
    op, depth, target, strict = case()
    _, _, report = bounded_inactive_fallback(depth, target, op, [False, True], strict, maximum_cells=0)
    assert report['status'] == 'unsupported_exception_scope'


def test_infeasibility_evidence_required():
    op, depth, target, _ = case()
    _, absolute, report = bounded_inactive_fallback(depth, target, op, [False, True], {})
    assert report['status'] == 'not_applied'
    np.testing.assert_array_equal(absolute, [.01, .01])
