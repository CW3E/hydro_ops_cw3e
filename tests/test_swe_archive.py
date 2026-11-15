import hashlib
import json

import pytest

from hydro_ops.observations import swe_archive as mod
from hydro_ops.observations.station_catalog import CATALOG_SCHEMA
from hydro_ops.observations.station_operations import COLLECTION, STATE


def setup(root):
    base = root/COLLECTION
    base.mkdir(parents=True)
    entries = []
    for sensor, resolution in [('3', 'daily'), ('3', 'monthly'), ('82', 'daily')]:
        name = f'cdec/ABC/snow_water_equivalent/{sensor}/{resolution}.nc'
        p = base/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(name.encode())
        entries.append({'provider': 'cdec', 'station_id': 'ABC', 'product': 'snow_water_equivalent',
            'sensor': sensor, 'resolution': resolution, 'path': name, 'bytes': p.stat().st_size,
            'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
    (base/'catalog.json').write_text(json.dumps({'schema': CATALOG_SCHEMA, 'entries': entries}))
    (root/STATE).mkdir(parents=True)
    (root/STATE/'active.json').write_text(json.dumps({'status': 'active', 'catalog': COLLECTION+'/catalog.json'}))
    return base, entries


def test_retire_and_retry(tmp_path):
    base, entries = setup(tmp_path)
    result = mod.run(tmp_path)
    assert result['phase'] == 'complete' and result['archived_files'] == 1
    assert not (base/entries[0]['path']).exists()
    assert (tmp_path/mod.ARCHIVE/entries[0]['path']).is_file()
    assert all((base/e['path']).exists() for e in entries[1:])
    assert mod.run(tmp_path)['archived_files'] == 1


def test_copy_failure_preserves_active(tmp_path, monkeypatch):
    base, entries = setup(tmp_path)
    original = (base/'catalog.json').read_bytes()
    def fail(*args):
        raise OSError('simulated copy failure')
    with monkeypatch.context() as m:
        m.setattr(mod, 'copy_atomic', fail)
        with pytest.raises(OSError):
            mod.run(tmp_path)
    assert (base/'catalog.json').read_bytes() == original
    assert all((base/e['path']).exists() for e in entries)
    assert mod.run(tmp_path)['phase'] == 'complete'
