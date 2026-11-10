import json

import pytest
from test_station_conversion import fixture

from hydro_ops.observations.station_catalog import read_member, write_catalog
from hydro_ops.observations.station_collection import finalize, require_coverage
from hydro_ops.observations.station_conversion import convert


def test_exact_coverage():
    require_coverage({'a', 'b'}, [{'a'}, {'b'}])


@pytest.mark.parametrize('groups', [[{'a'}], [{'a'}, {'a', 'b'}], [{'a', 'b', 'c'}]])
def test_incomplete_duplicate_extra(groups):
    with pytest.raises(ValueError):
        require_coverage({'a', 'b'}, groups)


@pytest.mark.parametrize('corrupt', [False, True])
def test_finalize_and_read(tmp_path, monkeypatch, corrupt):
    args, _, campaign = fixture(tmp_path, monkeypatch)
    result = convert(args)
    assert result['status'] == 'accepted'
    (campaign/'acceptance.json').write_text(json.dumps({'status': 'passed', 'passes': [[result]]}))
    target = next((campaign/'stations').glob('*.nc'))
    write_catalog(campaign/'catalog.json', [target], lock_root=campaign/'locks')
    catalog = campaign.parent/'collection.json'
    if corrupt:
        target.write_bytes(b'corrupt')
        with pytest.raises(ValueError, match='Stale'):
            finalize(tmp_path, 'inventory.json', [campaign], catalog)
        assert not catalog.exists()
        return
    report = finalize(tmp_path, 'inventory.json', [campaign], catalog)
    assert report['members'] == 1
    assert not report['production_modified']
    table = read_member(catalog, provider='cdec', station_id='AAA',
                        product='full_natural_flow', sensor='8', resolution='daily')
    assert len(table.rows) == 1
    assert finalize(tmp_path, 'inventory.json', [campaign], catalog)['publication']['status'] == 'unchanged'
