import os

from test_rain_repair_endpoints import module


def test_reviewed_scope_preserves_links_and_evidence(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'cleanup_reviewed_benchmarks')
    monkeypatch.setattr(mod.cleanup, 'ROOTS', mod.ROOTS)
    base = tmp_path/mod.ROOTS[0]
    base.mkdir(parents=True)
    array = base/'20000101.LDASIN_DOMAIN1'
    array.touch()
    assert mod.cleanup.eligible(tmp_path, array)
    for name in ('receipt.json', 'unknown.bin', 'RESTART.nc'):
        p = base/name
        p.touch()
        assert not mod.cleanup.eligible(tmp_path, p)
    os.link(array, base/'alias.nc')
    assert not mod.cleanup.eligible(tmp_path, array)
    other = tmp_path/'forcing/work/prism-rain-pipeline/test.nc'
    other.parent.mkdir(parents=True)
    other.touch()
    assert not mod.cleanup.eligible(tmp_path, other)
