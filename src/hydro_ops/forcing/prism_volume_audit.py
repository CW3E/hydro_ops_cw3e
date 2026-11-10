"""Area-weighted diagnostics, not new precipitation acceptance tolerances."""
import numpy as np


def region_totals(target, estimate, weights):
    target, estimate, weights = (np.asarray(x, dtype=float) for x in (target, estimate, weights))
    if target.shape != estimate.shape or target.shape != weights.shape:
        raise ValueError('Region arrays differ')
    used = weights > 0
    if not used.any() or np.any(~np.isfinite(weights)) or np.any(weights < 0):
        raise ValueError('Invalid region weights')
    if np.any(~np.isfinite(target[used])) or np.any(~np.isfinite(estimate[used])):
        raise ValueError('Missing supported precipitation')
    if np.any(target[used] < 0) or np.any(estimate[used] < -1e-8):
        raise ValueError('Negative supported precipitation')
    area = weights.sum()
    reference = float(np.sum(weights[used] * target[used]) / area)
    actual = float(np.sum(weights[used] * estimate[used]) / area)
    return {'prism_mean_mm': reference, 'forcing_mean_mm': actual,
            'bias_mm': actual - reference,
            'bias_percent': 100 * (actual - reference) / reference if reference > 0 else None}
