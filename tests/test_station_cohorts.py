import hashlib
import json

import pytest

from hydro_ops.observations.station_cohorts import filter_entries, scope_name


def test_partition_overlap_and_leading_zeros(tmp_path):
    (tmp_path/'config').mkdir()
    inventories = []
    for domain, content in [('cnrfc', '001\n002\n'), ('ucrb', 'USGS-002\n003\n')]:
        (tmp_path/domain).write_text(content)
        inventories.append({'path': domain, 'sha256': hashlib.sha256(content.encode()).hexdigest()})
    (tmp_path/'config/usgs_update_cohorts.json').write_text(json.dumps({'version': 1, 'inventories': inventories}))
    entries = [{'provider': 'usgs', 'station_id': 'USGS-'+v} for v in ['001', '002', '003', '004']]
    regional, proof = filter_entries(tmp_path, entries, 'regional')
    other, _ = filter_entries(tmp_path, entries, 'remainder')
    assert len(regional) == 3 and len(other) == 1
    assert regional+other == entries and proof['count'] == 3
    assert filter_entries(tmp_path, entries, 'all')[0] == entries
    (tmp_path/'ucrb').write_text('changed')
    with pytest.raises(ValueError, match='inventory changed'):
        filter_entries(tmp_path, entries, 'regional')


def test_scope_status_names():
    assert scope_name('usgs', 'all') == 'usgs'
    assert scope_name('usgs', 'regional') == 'usgs-regional'
    assert scope_name('usgs', 'remainder') == 'usgs-remainder'
    with pytest.raises(ValueError):
        scope_name('cdec-swe', 'regional')
