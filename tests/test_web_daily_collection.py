import json
from pathlib import Path

import pytest
from test_nwm_forcing_coordinator import load_script

from hydro_ops.web.globus_transfer import atomic_json, digest


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    return load_script('build_web_daily_collection.py')


def test_reuse_identity_content_and_stream_change(module, tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'data')
    item = {'source': 'source'}
    pin = module.renderer.identity(source)
    rows = [{'source': 'source', 'sourceIdentity': module.portable_identity(pin),
             'sourceSha256': digest(source)} for _ in range(2)]
    assert module.reusable(rows, item, pin, source)
    assert module.reusable(rows, item, [1, 2, 3], source)
    source.write_bytes(b'revised')
    assert not module.reusable(rows, item, [1, 2, 3], source)
    assert not module.reusable(rows, {'source': 'retro/source'}, pin, source)
    assert not module.reusable(rows[:1], item, pin, source)


def test_receipt_and_plan_pinning(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'render').mkdir()
    atomic_json(tmp_path / 'render/plan.json', {})
    atomic_json(tmp_path / 'upstream.json', {'status': 'complete'})
    atomic_json(tmp_path / 'cycle.json', {'pins': {}, 'upstreamReceipts': {
        'upstream.json': digest(tmp_path / 'upstream.json')},
        'renderPlanSha256': digest(tmp_path / 'render/plan.json')})
    module.checked(tmp_path)
    atomic_json(tmp_path / 'upstream.json', {'status': 'failed'})
    with pytest.raises(ValueError, match='Pinned'):
        module.checked(tmp_path)


def test_link_rejects_changed_or_conflicting_assets(module, tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'png')
    target = tmp_path / 'out/image.png'
    checksum = digest(source)
    module.link(source, target, checksum)
    module.link(source, target, checksum)
    assert source.stat().st_ino == target.stat().st_ino
    with pytest.raises(ValueError, match='checksum'):
        module.link(source, target, 'invalid')


def test_public_inventory_includes_display_policy(tmp_path):
    from hydro_ops.web.publication import raster_inventory
    for name, data in [('acceptance.json', {'status': 'passed'}),
                       ('catalog.json', {'families': []}), ('variables.json', {}), ('legends.json', {})]:
        atomic_json(tmp_path / name, data)
    (tmp_path / 'provenance').mkdir()
    atomic_json(tmp_path / 'provenance/mexico.geojson', {})
    atomic_json(tmp_path / 'display-policy.json', {'boundaryGeometry': 'provenance/mexico.geojson',
        'boundaryGeometrySha256': digest(tmp_path / 'provenance/mexico.geojson')})
    result = raster_inventory(tmp_path)
    assert {'display-policy.json', 'provenance/mexico.geojson'} <= {r['path'] for r in result['files']}
    (tmp_path / 'acceptance.json').rename(tmp_path / 'pending-acceptance.json')
    with pytest.raises(FileNotFoundError):
        raster_inventory(tmp_path)
    assert raster_inventory(tmp_path, acceptance_path=tmp_path / 'pending-acceptance.json') == result
    (tmp_path / 'pending-acceptance.json').rename(tmp_path / 'acceptance.json')
    (tmp_path / 'provenance/mexico.geojson').write_text(json.dumps({'changed': True}))
    with pytest.raises(ValueError, match='boundary checksum'):
        raster_inventory(tmp_path)


def test_real_revision_comparison_distinguishes_pixels_from_sources(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = load_script('test_web_real_revision.py')
    for domain in ('cnrfc', 'ucrb'):
        for generation in ('old', 'new'):
            folder = tmp_path / generation / domain
            folder.mkdir(parents=True)
            color = (255, 0, 0, 255) if generation == 'new' and domain == 'cnrfc' else (0, 0, 0, 0)
            Image.new('RGBA', (1, 1), color).save(folder / 'image.png')
            atomic_json(folder / 'availability.json', {'assets': [{
                'id': 'sample', 'href': 'image.png', 'variable': 'temperature',
                'date': '2026-11-02', 'source': 'source', 'sourceSha256': generation,
                'sha256': digest(folder / 'image.png')}]})
    rows = module.compare(tmp_path / 'old', tmp_path / 'new')
    assert all(r['sourceContentChanged'] for r in rows)
    assert rows[0]['changedPixels'] == 1 and rows[0]['pngChanged']
    assert rows[1]['changedPixels'] == 0 and not rows[1]['pngChanged']
