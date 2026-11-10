from copy import deepcopy

import pytest

from hydro_ops.observations.station_store import StationTable, read_station, write_station
from hydro_ops.observations.station_updates import merge_window, refresh_station


def sample():
    return StationTable(
        {'provider': 'test', 'station_id': '01', 'product': 'flow', 'sensor': '8', 'resolution': 'daily',
             'units_si': 'm3 s-1', 'timezone': 'PST', 'interval_semantics': 'provider labels',
             'mapping': {'series': 'series', 'date': 'day', 'value': 'value', 'status': 'flag'}},
        ['series', 'day', 'value', 'flag'],
        [{'series': 'a', 'day': f'2020-01-0{i}', 'value': str(i), 'flag': 'numeric'} for i in range(1, 4)])


def test_window_revision_extension_missing_and_noop():
    old = sample()
    incoming = deepcopy(old)
    incoming.rows = incoming.rows[1:]
    incoming.rows[0].update(value='', flag='missing')
    incoming.rows.append({'series': 'a', 'day': '2020-01-04', 'value': '0', 'flag': 'numeric'})
    result = merge_window(old, incoming, start='2020-01-02', end='2020-01-04')
    assert result.rows[0] == old.rows[0]
    assert result.rows[1]['value'] == ''
    assert result.rows[-1]['value'] == '0'
    assert len(result.rows) == 4
    assert merge_window(result, incoming, start='2020-01-02', end='2020-01-04') == result
    assert len(old.rows) == 3


@pytest.mark.parametrize('failure', ['omitted', 'outside', 'series', 'schema', 'identity', 'bounds'])
def test_fail_closed(failure):
    old, incoming = sample(), sample()
    if failure == 'omitted':
        incoming.rows.pop()
    elif failure == 'outside':
        incoming.rows[-1]['day'] = '2020-01-04'
    elif failure == 'series':
        incoming.rows.append({'series': 'b', 'day': '2020-01-01', 'value': '1', 'flag': 'numeric'})
    elif failure == 'schema':
        incoming.fields.reverse()
    elif failure == 'identity':
        incoming.metadata['timezone'] = 'UTC'
    with pytest.raises(ValueError):
        merge_window(old, incoming, start='2020-01-01',
                     end='2019-12-31' if failure == 'bounds' else '2020-01-03')


def test_monthly_leap_and_empty_extension():
    old = sample()
    old.metadata['resolution'] = 'monthly'
    for row, month in zip(old.rows, ('2020-01', '2020-02', '2020-03'), strict=True):
        row['day'] = month
    incoming = deepcopy(old)
    incoming.rows = [incoming.rows[1]]
    assert merge_window(old, incoming, start='2020-02', end='2020-02') == old
    incoming.rows = []
    assert merge_window(old, incoming, start='2020-04', end='2020-04') == old
    with pytest.raises(ValueError, match='omitted'):
        merge_window(old, incoming, start='2020-02', end='2020-02')


def test_staged_refresh_and_stale_writer(tmp_path):
    target = tmp_path/'observations/work/pilot/station.nc'
    locks = tmp_path/'locks'
    old = sample()
    write_station(target, old, lock_root=locks)
    incoming = deepcopy(old)
    incoming.rows[1]['value'] = '42'
    assert refresh_station(tmp_path, target, incoming, start='2020-01-01',
                           end='2020-01-03', lock_root=locks)['status'] == 'published'
    accepted = read_station(target)
    with pytest.raises(ValueError, match='changed during refresh'):
        write_station(target, old, lock_root=locks, expected_signature=old.signature())
    assert read_station(target) == accepted
    with pytest.raises(ValueError, match='observations/work'):
        refresh_station(tmp_path, tmp_path/'production.nc', incoming,
                        start='2020-01-01', end='2020-01-03', lock_root=locks)
