from datetime import date

from hydro_ops.forcing.prism_revision_queue import acknowledge, affected_days, enqueue


def test_both_streams_halos_and_durable_pending():
    assert affected_days(date(2026, 1, 1)) == (date(2025, 12, 31), date(2026, 1, 1))
    sources = {'ppt/2026-01-01': {'signature': 'a'}}
    state = enqueue({}, sources, lambda stream, day: True)
    assert len(state['pending']) == 4
    enqueue(state, sources, lambda stream, day: True)
    assert len(state['pending']) == 4
    key = 'nrt/2026-01-01'
    old = dict(state['pending'][key]['sources'])
    enqueue(state, {'ppt/2026-01-01': {'signature': 'b'}}, lambda stream, day: True)
    accepted = {'status': 'passed', 'hourly': True, 'summaries': True, 'subsets': True}
    assert not acknowledge(state, key, old, accepted)
    current = state['pending'][key]['sources']
    assert not acknowledge(state, key, current, accepted | {'subsets': False})
    assert acknowledge(state, key, current, accepted)
    assert len(state['pending']) == 3


def test_missing_targets_not_created_and_temperature_tracked():
    state = enqueue({}, {'tmin/2026-01-01': {'signature': 'a'}},
                    lambda stream, day: stream == 'nrt' and day == date(2026, 1, 1))
    assert list(state['pending']) == ['nrt/2026-01-01']
