import pytest

from hydro_ops.observations.station_batches import batches


def report(n):
    return {'entries': [{'task_id': str(i), 'status': 'candidate', 'provider': 'test',
                         'product': str(i % 3), 'sensor': '1', 'resolution': 'daily'} for i in range(n)]}


def test_disjoint_complete_and_bounded():
    plan = batches(report(8863), excluded={str(i) for i in range(160)})
    assert len(plan) == 68
    assert all(1 <= len(b) <= 128 for b in plan)
    flat = [t for b in plan for t in b]
    assert len(flat) == len(set(flat)) == 8703
    assert set(flat) == {str(i) for i in range(160, 8863)}


def test_empty_blocked_and_unknown_exclusion():
    data = report(3)
    data['entries'][0]['status'] = 'blocked'
    assert batches(data, excluded={'1'}) == [['2']]
    assert batches(data, excluded={'1', '2'}) == []
    with pytest.raises(ValueError):
        batches(data, excluded={'0'})
