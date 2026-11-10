import io
import os

from test_rain_repair_endpoints import module


def test_inventory_does_not_follow_links_or_delete(tmp_path, monkeypatch):
    mod = module(monkeypatch, 'inventory_historical_work')
    work = tmp_path/'work'
    work.mkdir()
    (work/'array.nc').write_bytes(b'abc')
    (work/'report.json').write_text('{}')
    (work/'RESTART.nc').write_bytes(b'rst')
    outside = tmp_path/'outside'
    outside.mkdir()
    (outside/'secret.nc').write_bytes(b'keep')
    (work/'linked').symlink_to(outside, target_is_directory=True)
    os.link(work/'array.nc', work/'alias.nc')
    ledger = io.StringIO()
    result = mod.inventory(work, tmp_path, ledger)
    assert result['counts']['directory_symlink'] == 1
    assert result['counts']['hardlinked_review'] == 2
    assert result['counts']['protected_recovery'] == 1
    assert result['counts']['retain_evidence'] == 1
    assert 'secret.nc' not in ledger.getvalue()
    assert (work/'array.nc').read_bytes() == b'abc'
