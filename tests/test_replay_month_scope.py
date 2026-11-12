from datetime import date

import pytest

from hydro_ops.forcing.replay_month_scope import month_scope
from hydro_ops.forcing.temporal_summary import summary_path


def test_open_month_and_boundary(tmp_path):
    assert month_scope(tmp_path, 'nrt', date(2026, 11, 4), defer_open_month=True,
                       today=date(2026, 11, 12)) == {
        'months': [], 'deferred_months': ['2026-11-01'], 'policy': 'closed-months-only'}
    scope = month_scope(tmp_path, 'nrt', date(2026, 11, 1), defer_open_month=True,
                        today=date(2026, 11, 12))
    assert scope['months'] == ['2026-10-01']
    assert scope['deferred_months'] == ['2026-11-01']
    assert month_scope(tmp_path, 'nrt', date(2026, 10, 5), defer_open_month=True,
                       today=date(2026, 11, 12))['months'] == ['2026-10-01']


def test_never_defers_closed_or_retro_months(tmp_path):
    scope = month_scope(tmp_path, 'nrt', date(2026, 11, 4), defer_open_month=True,
                        today=date(2026, 12, 1))
    assert scope['months'] == ['2026-11-01']
    assert not scope['deferred_months']
    with pytest.raises(ValueError, match='past NRT'):
        month_scope(tmp_path, 'retro', date(2026, 11, 4), defer_open_month=True)


def test_existing_monthly_product_cannot_be_left_stale(tmp_path):
    target = summary_path(tmp_path/'forcing/outputs/yampa/nrt/monthly', date(2026, 11, 1), monthly=True)
    target.parent.mkdir(parents=True)
    target.touch()
    with pytest.raises(ValueError, match='existing monthly'):
        month_scope(tmp_path, 'nrt', date(2026, 11, 4), defer_open_month=True,
                    today=date(2026, 11, 12))
