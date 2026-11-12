import json

import pytest

from hydro_ops.forcing import nrt_cycle
from hydro_ops.forcing.rain_repair_writer import digest
from hydro_ops.forcing.replay_baseline_cache import import_baseline


def fixture(tmp_path):
    source = tmp_path/'source'
    source.write_bytes(b'accepted baseline')
    receipt = {'status': 'passed', 'complete_day': True, 'day': '2026-10-01',
               'input_fingerprint': 'current', 'published_identity': nrt_cycle.identity(source),
               'sha256': digest(source)}
    sidecar = source.with_name(source.name+'.nrt-receipt.json')
    sidecar.write_text(json.dumps(receipt))
    return source, sidecar, receipt


def test_verified_copy_is_independent(tmp_path):
    source, _, receipt = fixture(tmp_path)
    target = tmp_path/'isolated'/'target'
    result = import_baseline(source, target, 'current', '2026-10-01')
    assert result['sha256'] == receipt['sha256']
    assert nrt_cycle.up_to_date(target, result, 'current')
    assert source.stat().st_ino != target.stat().st_ino
    target.write_bytes(b'changed isolated copy')
    assert digest(source) == receipt['sha256']


@pytest.mark.parametrize('change', ['inputs', 'incomplete', 'checksum', 'date', 'retry'])
def test_reject_invalid_cache(tmp_path, change):
    source, sidecar, receipt = fixture(tmp_path)
    if change == 'inputs':
        receipt['input_fingerprint'] = 'old'
    elif change == 'incomplete':
        receipt['complete_day'] = False
    elif change == 'checksum':
        receipt['sha256'] = 'wrong'
    elif change == 'date':
        receipt['day'] = '2026-10-02'
    else:
        receipt['retry_preferred_gfs_cycle'] = True
    sidecar.write_text(json.dumps(receipt))
    target = tmp_path/'target'
    assert import_baseline(source, target, 'current', '2026-10-01') is None
    assert not target.exists()


def test_source_change_during_copy(tmp_path, monkeypatch):
    from hydro_ops.forcing import replay_baseline_cache as cache
    source, sidecar, _ = fixture(tmp_path)
    copy = cache.shutil.copy2
    def changed(src, dst):
        copy(src, dst)
        sidecar.write_text('{}')
    monkeypatch.setattr(cache.shutil, 'copy2', changed)
    with pytest.raises(ValueError, match='changed during import'):
        import_baseline(source, tmp_path/'target', 'current', '2026-10-01')
    assert not (tmp_path/'target').exists()
    assert not (tmp_path/'target.cache-import').exists()
