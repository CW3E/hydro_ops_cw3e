import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def load():
    path = Path(__file__).resolve().parents[1] / 'bin/test_web_live_bootstrap.py'
    spec = importlib.util.spec_from_file_location('live_bootstrap', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_requires_retention_and_exact_public_metadata(tmp_path, monkeypatch):
    module = load()
    (tmp_path / 'catalog.json').write_bytes(b'catalog')
    inventory = {'files': [{'path': 'catalog.json', 'role': 'catalog'},
                           {'path': 'image.png', 'role': 'payload'}]}
    monkeypatch.setattr(module, 'raster_inventory', lambda p: inventory)
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs['allow_redirects'] is False
        return SimpleNamespace(status_code=200, content=b'catalog')

    config = {'public_base_url': 'https://example.test/nwm/'}
    result = module.preflight(tmp_path, inventory, config, get)
    assert result['legacyPathsRetained'] == 2 and result['deletions'] == []
    assert calls == ['https://example.test/nwm/catalog.json']
    with pytest.raises(ValueError, match='abandoned'):
        module.preflight(tmp_path, {'files': inventory['files'][:1]}, config, get)
    with pytest.raises(ValueError, match='differs'):
        module.preflight(tmp_path, inventory, config,
                         lambda *a, **kw: SimpleNamespace(status_code=200, content=b'changed'))
