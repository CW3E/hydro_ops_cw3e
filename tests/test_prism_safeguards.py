import numpy as np

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_safeguards import (
    allocate_daily_depth,
    audit_daily_constraint,
    fit_daily_depth,
    repair_local_fit,
    weak_support_targets,
)


def identity(n):
    return ConservativeOperator(n, n, np.arange(n), np.arange(n), np.ones(n))


def test_local_fallback_and_mass():
    baseline = np.zeros((24, 5))
    baseline[0] = [2, 0, 0.001, 0, 0]
    nldas = np.zeros_like(baseline)
    nldas[7, 1] = 3
    target = np.array([4, 20, 10, 0, np.nan])
    hourly, provenance = allocate_daily_depth(target, baseline, nldas)
    np.testing.assert_array_equal(provenance, [1, 2, 3, 4, 0])
    np.testing.assert_allclose(hourly.sum(axis=0), target)
    assert hourly[7, 1] == 20
    np.testing.assert_allclose(hourly[:, 2], 10 / 24)
    assert audit_daily_constraint(hourly, target, identity(5))["accepted"]


def test_cap_exemptions_cannot_pass():
    hourly = np.zeros((24, 2))
    hourly[0, 1] = 1
    result = audit_daily_constraint(hourly, [10, 20], identity(2))
    assert not result["accepted"]
    assert result["failed_cells"] == 2


def test_partial_support_cannot_pass_normalized_operator():
    operator = ConservativeOperator(2, 1, np.array([0, 1]), np.array([0, 0]), np.ones(2))
    hourly = np.ones((24, 2))
    hourly[0, 1] = np.nan
    assert not audit_daily_constraint(hourly, [24], operator)["accepted"]


def test_incomplete_profile_falls_back_and_extreme_ratio_does_not_cap_mass():
    baseline = np.ones((24, 2))
    baseline[0, 0] = np.nan
    nldas = np.zeros_like(baseline)
    nldas[12] = 1
    hourly, source = allocate_daily_depth(np.array([10, 300]), baseline, nldas)
    np.testing.assert_array_equal(source, [2, 2])
    np.testing.assert_allclose(hourly[12], [10, 300])


def test_no_target_is_not_acceptance():
    assert not audit_daily_constraint(np.zeros((24, 1)), [np.nan], identity(1))["accepted"]


def test_spatial_fit_seeds_dry_baseline_and_handles_overlap():
    operator = ConservativeOperator(3, 2, np.array([0, 1, 1, 2]),
                                    np.array([0, 0, 1, 1]), np.ones(4))
    depth, _ = fit_daily_depth(np.array([10, 20]), operator)
    hourly, _ = allocate_daily_depth(depth, np.zeros((24, 3)), np.zeros((24, 3)))
    assert audit_daily_constraint(hourly, [10, 20], operator)["accepted"]


def test_incompatible_targets_are_rejected():
    operator = ConservativeOperator(1, 2, np.array([0, 0]), np.array([0, 1]), np.ones(2))
    depth, _ = fit_daily_depth(np.array([0, 20]), operator, max_iterations=10)
    hourly, _ = allocate_daily_depth(depth, np.zeros((24, 1)), np.zeros((24, 1)))
    assert not audit_daily_constraint(hourly, [0, 20], operator)["accepted"]


def test_local_fit_revives_zero_and_preserves_exterior():
    depth, report = repair_local_fit(np.array([0., 20.]), np.array([10., 20.]), identity(2))
    assert report['applied']
    assert abs(depth[0]-10) <= .02
    assert depth[1] == 20


def test_local_fit_cannot_hide_incompatible_neighbor():
    operator = ConservativeOperator(1, 2, np.array([0, 0]), np.array([0, 1]), np.ones(2))
    depth, report = repair_local_fit(np.array([10.]), np.array([0., 20.]), operator)
    assert not report['applied']
    assert report['status'] == 'local_solve_failed'
    np.testing.assert_array_equal(depth, [10.])


def test_local_fit_safety_margin_can_fail_without_failing_original_gate():
    operator = ConservativeOperator(1, 2, np.array([0, 0]), np.array([0, 1]), np.ones(2))
    target = np.array([.612, .632])
    _, strict = repair_local_fit(np.array([.61]), target, operator)
    assert not strict['applied']
    assert .5 < strict['minimum_tolerance_multiplier'] < 1
    depth, fallback = repair_local_fit(np.array([.61]), target, operator, safety_margin=.99)
    assert fallback['applied']
    hourly, _ = allocate_daily_depth(depth, np.zeros((24, 1)), np.zeros((24, 1)))
    assert audit_daily_constraint(hourly, target, operator)['accepted']


def test_weak_support_preserves_active_and_threshold_boundary():
    result = weak_support_targets(identity(6), [.001, .049, .05, .9, np.nan, .001],
                                  [False, False, False, False, False, True])
    np.testing.assert_array_equal(result, [True, True, False, False, False, False])


def test_any_active_contributor_prevents_weak_classification():
    operator = ConservativeOperator(2, 1, np.array([0, 1]), np.array([0, 0]),
                                    np.array([.999999, .000001]))
    assert not weak_support_targets(operator, [.001], [False, True])[0]


def test_candidate_ten_percent_support_still_protects_active_cells():
    fractions = [.0584317448, .098, .10, .98881227]
    active = [False, True, False, True]
    np.testing.assert_array_equal(
        weak_support_targets(identity(4), fractions, active, threshold=.10),
        [True, False, False, False],
    )
    assert not weak_support_targets(identity(4), fractions, active)[0]
