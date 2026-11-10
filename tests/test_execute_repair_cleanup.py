import os

import pytest
from test_rain_repair_endpoints import module


def test_exact_targets_links_and_replacement_checks(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'execute_forcing_repair_cleanup')
    relative = '1981/01/19810101.LDASIN_DOMAIN1'
    old = tmp_path/'forcing/work/prism-repair-wave1-198101-198108/originals'/relative
    target = tmp_path/'forcing/outputs/conus/retro/hourly'/relative
    for p in (old, target):
        p.parent.mkdir(parents=True)
        p.write_text(str(p))
    alias = old.with_name('alias')
    os.link(old, alias)
    entry = {'delete': mod.file_record(old), 'replacement': mod.file_record(target), 'retain_evidence': []}
    assert mod.validate(entry, tmp_path) == old
    alias.unlink()
    with pytest.raises(ValueError, match='Hard links changed'):
        mod.validate(entry, tmp_path)
    assert mod.validate(entry, tmp_path, expected_links=1) == old
    target.write_text('changed replacement')
    with pytest.raises(ValueError, match='Replacement changed'):
        mod.validate(entry, tmp_path, expected_links=1)


def test_canonical_path_cannot_be_deleted(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'execute_forcing_repair_cleanup')
    p = tmp_path/'forcing/outputs/conus/retro/hourly/1981/01/19810101.LDASIN_DOMAIN1'
    p.parent.mkdir(parents=True)
    p.touch()
    entry = {'delete': mod.file_record(p), 'replacement': mod.file_record(p), 'retain_evidence': []}
    with pytest.raises(ValueError):
        mod.validate(entry, tmp_path)


def test_retired_snapshot_fails_explicitly(tmp_path):
    from hydro_ops.forcing.repair_transaction import atomic_json, check_snapshot
    path = tmp_path/'snapshot.json'
    atomic_json(path, {'status': 'passed', 'snapshot_root': str(tmp_path), 'files': []})
    atomic_json(tmp_path/'RETIRED.json', {'status': 'retired'})
    with pytest.raises(ValueError, match='retired'):
        check_snapshot(path, tmp_path)
