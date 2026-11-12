"""Explicit monthly obligations for NRT replay before the current month closes."""
from datetime import UTC, datetime, timedelta

from hydro_ops.forcing.temporal_summary import summary_path

DOMAINS = ('conus', 'cnrfc', 'b120', 'ucrb', 'yampa')


def month_scope(root, stream, day, *, defer_open_month=False, today=None):
    today = today or datetime.now(UTC).date()
    affected = sorted({d.replace(day=1) for d in (day-timedelta(days=1), day)})
    deferred = []
    if defer_open_month:
        if stream != 'nrt' or day >= today:
            raise ValueError('Open-month deferral requires a past NRT date')
        deferred = [m for m in affected if m == today.replace(day=1)]
        for month in deferred:
            for domain in DOMAINS:
                path = summary_path(root/'forcing/outputs'/domain/stream/'monthly', month, monthly=True)
                if path.exists():
                    raise ValueError(f'Cannot defer replacement of an existing monthly product: {path}')
    return {'months': [str(m) for m in affected if m not in deferred],
            'deferred_months': list(map(str, deferred)),
            'policy': 'closed-months-only' if defer_open_month else 'all-affected-months'}


def validate_scope(root, stream, day, scope):
    expected = month_scope(root, stream, day,
                           defer_open_month=scope['policy'] == 'closed-months-only')
    if scope != expected:
        raise ValueError('Monthly scope changed; replan replay (including month rollover)')
    return expected
