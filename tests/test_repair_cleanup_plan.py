import os

import pytest
from test_rain_repair_endpoints import module


def test_hardlink_reclaim_estimate(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'plan_forcing_repair_cleanup')
    a, b = tmp_path/'a', tmp_path/'b'
    a.write_bytes(b'x'*8192)
    os.link(a, b)
    rows = [{'status': 'eligible_for_review', 'delete': mod.file_record(a)}]
    assert mod.reclaim_estimate(rows)['reclaimable_logical_bytes'] == 0
    rows.append({'status': 'eligible_for_review', 'delete': mod.file_record(b)})
    assert mod.reclaim_estimate(rows)['reclaimable_logical_bytes'] == 8192
    assert mod.reclaim_estimate(rows)['inodes_with_all_links_in_plan'] == 1


def test_current_data_and_changed_original_protected(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'plan_forcing_repair_cleanup')
    target = tmp_path/'forcing/outputs/conus/retro/hourly/1981/01/19810101.LDASIN_DOMAIN1'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'current')
    original = tmp_path/'forcing/work/wave/original'
    original.parent.mkdir(parents=True)
    os.link(target, original)
    args = (tmp_path, original, target, list(mod.fingerprint(original)), original.parent/'snapshot.json')
    assert mod.check(args)['status'] == 'retained'
    changed = (*args[:3], [0, 0, 0], args[4])
    assert mod.check(changed)['status'] == 'blocked'
    link = tmp_path/'symlink'
    link.symlink_to(target)
    with pytest.raises(ValueError, match='Nonregular'):
        mod.file_record(link)
