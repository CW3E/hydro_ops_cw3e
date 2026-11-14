import json
from copy import deepcopy
from datetime import date, timedelta

import pytest

from hydro_ops.web.globus_transfer import atomic_json, digest
from hydro_ops.web.history import continuous_coverage, merge, select


def row(day='2026-11-10', stream='nrt', variable='temperature', product='forcing'):
    return {'date': day, 'stream': stream, 'variable': variable, 'product': product,
            'resolution': 'daily', 'id': f'{stream}_{variable}_{day}',
            'href': f'images/{stream}_{variable}_{day}.png', 'sha256': 'a' * 64,
            'validInterval': {'start': day + 'T00:00:00Z',
                'end': str(date.fromisoformat(day) + timedelta(days=1)) + 'T00:00:00Z'}}


def test_selection_never_shrinks_and_allows_retro_promotion():
    old = [row(), row('2026-11-11')]
    refresh = [row('2026-11-11', 'retro'), row('2026-11-12')]
    result, counts = select(old, refresh)
    assert len(result) == 3
    assert counts['retainedImages'] == counts['replacedImages'] == counts['addedImages'] == 1
    assert counts['lostLogicalAssets'] == 0
    with pytest.raises(ValueError, match='downgrade'):
        select([row(stream='retro')], [row()])
    with pytest.raises(ValueError, match='Duplicate'):
        select([row(), row(stream='retro')], [])
    bad = row()
    bad['validInterval']['end'] = '2026-11-12T00:00:00Z'
    with pytest.raises(ValueError, match='bounds'):
        select([row()], [bad])


def fixture(folder, days):
    folder.mkdir()
    (folder / 'cnrfc/images').mkdir(parents=True)
    (folder / 'provenance').mkdir()
    atomic_json(folder / 'provenance/mexico.geojson', {})
    policy = {'boundaryGeometry': 'provenance/mexico.geojson',
              'boundaryGeometrySha256': digest(folder / 'provenance/mexico.geojson')}
    for name, value in [('display-policy.json', policy), ('variables.json', {}), ('legends.json', {}),
                        ('plan.json', {}), ('acceptance.json', {'status': 'passed'})]:
        atomic_json(folder / name, value)
    rows = []
    for day in days:
        for product, variable in [('forcing', 'temperature'), ('forcing', 'precipitation'),
                                  ('nwm', 'soil_water_percentile'), ('nwm', 'swe_percentile')]:
            asset = row(day, variable=variable, product=product)
            path = folder / 'cnrfc' / asset['href']
            path.write_bytes((day + variable).encode())
            asset['sha256'] = digest(path)
            rows.append(asset)
    family = {'id': 'cnrfc', 'domain': 'cnrfc', 'pixelProjection': 'EPSG:3857',
              'projectedExtent': [0, 0, 1, 1], 'placement': {}, 'width': 1, 'height': 1,
              'availability': 'availability.json', 'timeline': 'timeline.json',
              'coverage': 'coverage.json', 'streamPolicy': 'exclusive'}
    for name, data in [('family.json', family), ('availability.json', {'assets': rows}),
                       ('timeline.json', {}), ('coverage.json', {})]:
        atomic_json(folder / 'cnrfc' / name, data)
    atomic_json(folder / 'catalog.json', {'families': [
        {'id': 'cnrfc', 'href': 'cnrfc/family.json', 'images': len(rows)}]})
    return rows


def test_merge_preserves_old_images_and_repeat(tmp_path):
    base, window = tmp_path / 'base', tmp_path / 'window'
    old = fixture(base, ['2026-11-10', '2026-11-11'])
    fixture(window, ['2026-11-11', '2026-11-12'])
    output = tmp_path / 'full'
    report = merge(base, window, output)
    assert report['status'] == 'passed'
    family = report['families'][0]
    assert family['images'] == 12 and family['lostLogicalAssets'] == 0
    assert family['retainedImages'] == family['addedImages'] == 4
    for asset in old:
        assert digest(output / 'cnrfc' / asset['href']) == asset['sha256']
    repeated = merge(output, window, tmp_path / 'repeat')
    assert repeated['families'][0]['images'] == 12
    with pytest.raises(ValueError, match='fresh'):
        merge(base, window, output)


def test_gaps_and_grid_changes_block_acceptance(tmp_path):
    base, window = tmp_path / 'base', tmp_path / 'window'
    rows = fixture(base, ['2026-11-10'])
    fixture(window, ['2026-11-12'])
    with pytest.raises(ValueError, match='gaps'):
        merge(base, window, tmp_path / 'gap')
    assert not (tmp_path / 'gap/acceptance.json').exists()
    bad = deepcopy(rows)
    bad.pop()
    with pytest.raises(ValueError, match='gaps'):
        continuous_coverage(bad)
    path = window / 'cnrfc/family.json'
    family = json.loads(path.read_text())
    family['width'] = 2
    atomic_json(path, family)
    with pytest.raises(ValueError, match='grid'):
        merge(base, window, tmp_path / 'grid')
    assert not (tmp_path / 'grid/acceptance.json').exists()
    atomic_json(window / 'legends.json', {'changed': True})
    with pytest.raises(ValueError, match='definitions'):
        merge(base, window, tmp_path / 'legend')
