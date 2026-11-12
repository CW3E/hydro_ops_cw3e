from types import SimpleNamespace

from hydro_ops.observations.station_cleanup import compare_history, legacy_payload


def test_only_processed_payloads_are_candidates():
    assert legacy_payload('observations/processed/usgs/discharge/daily/USGS-01/history.csv.gz')
    assert legacy_payload('observations/processed/cdec/fnf/daily/AMF/2025.csv')
    assert legacy_payload('observations/processed/cdec/swe/sensor_3/monthly/ABC/2025.csv.gz')
    for path in ['observations/inputs/usgs/discharge/history.csv.gz',
                 'observations/processed/usgs/discharge/daily/USGS-01/annual_completeness.csv',
                 'observations/processed/cdec/fnf/daily/AMF/manifest.json']:
        assert not legacy_payload(path)


def test_exact_superset_and_revisions_retained():
    def table(rows):
        return SimpleNamespace(metadata={'mapping': {'series': 'id', 'date': 'day'}},
                               fields=['id', 'day', 'flag'], rows=rows)
    original = {'id': '0001', 'day': '2020-01-01', 'flag': 'P'}
    old = table([original])
    assert compare_history(old, table([original, dict(original, day='2020-01-02')]))['status'] == 'eligible'
    assert compare_history(old, table([dict(original, flag='A')]))['changed_count'] == 1
    assert compare_history(old, table([]))['missing_count'] == 1
