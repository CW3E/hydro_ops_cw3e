import json
from datetime import date, timedelta

import pytest

from hydro_ops.forcing.prism_maturity import (
    apply_queue_policy,
    day_ready,
    input_root,
    paths,
    pin_nrt_day,
    policy,
    release_ready,
    selected_nrt_paths,
)
from hydro_ops.forcing.prism_revision_queue import source_signature


def setup(root):
    (root/'config').mkdir()
    (root/'config/prism_revision_policy.toml').write_text(
        'enabled = true\nnrt_window_days = 11\nnrt_grid_count = 2\nretro_minimum_grid_count = 8\n')


def source(root, day, count):
    result = {}
    for p in paths(input_root(root, 'retro'), day):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f'grid {count}'.encode())
        p.with_suffix('.release.json').write_text(json.dumps({'grid_count': count, 'data_date': str(day)}))
        var = p.name.split('_')[1]
        result[f'{var}/{day}'] = source_signature(p)
    return result


def test_pin_exact_second_release_survives_updates(tmp_path):
    setup(tmp_path)
    d, today = date(2026, 11, 4), date(2026, 11, 11)
    source(tmp_path, d, 1)
    assert pin_nrt_day(tmp_path, d, today)['status'] == 'awaiting_second_release'
    source(tmp_path, d, 2)
    record = pin_nrt_day(tmp_path, d, today)
    assert record['status'] == 'selected'
    source(tmp_path, d, 3)
    assert pin_nrt_day(tmp_path, d, today) == record
    assert all(p.read_bytes() == b'grid 2' for p in paths(input_root(tmp_path, 'nrt'), d))
    assert not day_ready(tmp_path, d, 'nrt')
    source(tmp_path, d+timedelta(days=1), 2)
    pin_nrt_day(tmp_path, d+timedelta(days=1), today)
    assert day_ready(tmp_path, d, 'nrt')
    assert all(p.is_file() for p in selected_nrt_paths(tmp_path, d))
    paths(input_root(tmp_path, 'nrt'), d)[0].write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='changed'):
        pin_nrt_day(tmp_path, d, today)


def test_no_late_substitution_or_unstable_retro(tmp_path):
    setup(tmp_path)
    d, today = date(2026, 11, 4), date(2026, 11, 11)
    source(tmp_path, d, 3)
    assert pin_nrt_day(tmp_path, d, today)['status'] == 'second_release_missed'
    assert not release_ready(paths(input_root(tmp_path, 'retro'), d)[0], 'retro')
    source(tmp_path, d, 8)
    assert release_ready(paths(input_root(tmp_path, 'retro'), d)[0], 'retro')
    assert pin_nrt_day(tmp_path, d, today+timedelta(days=20))['status'] == 'outside_nrt_window'


def test_queue_skips_later_nrt_revisions_without_false_ack(tmp_path):
    setup(tmp_path)
    d, today = date(2026, 11, 4), date(2026, 11, 11)
    for stream in ('nrt', 'retro'):
        p = tmp_path/f'forcing/outputs/conus/{stream}/hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        p.parent.mkdir(parents=True); p.touch()
    sources = source(tmp_path, d, 2)
    state = apply_queue_policy({}, tmp_path, today, sources)
    assert list(state['pending']) == [f'nrt/{d}']
    state['pending'].clear()  # Simulate accepted publication.
    sources = source(tmp_path, d, 3)
    apply_queue_policy(state, tmp_path, today, sources)
    assert not state['pending']
    sources = source(tmp_path, d, 8)
    apply_queue_policy(state, tmp_path, today, sources)
    assert list(state['pending']) == [f'retro/{d}']
    state['pending'][f'nrt/{d}'] = {'stream': 'nrt', 'day': str(d), 'sources': {'ppt/'+str(d): 'old'}}
    apply_queue_policy(state, tmp_path, today+timedelta(days=20), sources)
    assert f'nrt/{d}' not in state['pending']
    assert state['policy_dispositions'][f'nrt/{d}']['status'] == 'not_applied_policy'


def test_policy_migration_does_not_requeue_known_stable_sources(tmp_path):
    setup(tmp_path)
    d = date(2026, 1, 1)
    p = tmp_path/'forcing/outputs/conus/retro/hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    p.parent.mkdir(parents=True); p.touch()
    sources = source(tmp_path, d, 8)
    state = {'sources': sources, 'pending': {}}
    apply_queue_policy(state, tmp_path, date(2026, 11, 11), sources)
    assert not state['pending']


def test_daily_cycle_window_is_bounded_when_enabled(tmp_path, monkeypatch):
    from test_nwm_forcing_coordinator import load_script
    setup(tmp_path)
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    m = load_script('update_nwm_forcing.py')
    result = m.cycle_window('daily', date(2026, 11, 11))
    assert result[1:4] == (date(2026, 10, 31), date(2026, 11, 9), 14)


def test_activation_gate(tmp_path):
    setup(tmp_path)
    config = tmp_path/'config/prism_revision_policy.toml'
    config.write_text(config.read_text()+'activation_report = "activation.json"\n')
    assert not policy(tmp_path)['enabled']
    (tmp_path/'activation.json').write_text(json.dumps({'status': 'failed', 'policy': 'two_maturity_stages_v1'}))
    assert not policy(tmp_path)['enabled']
    (tmp_path/'activation.json').write_text(json.dumps({'status': 'passed', 'policy': 'two_maturity_stages_v1'}))
    assert policy(tmp_path)['enabled']
