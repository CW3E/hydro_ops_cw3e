from types import SimpleNamespace

import pytest

from hydro_ops.web.station_pilot import (
    collection_index,
    display_value,
    export_rows,
    layer_notes,
    plot_expectation,
    refine_contract,
    release_metadata,
    water_years,
)


def test_full_publication_contract_not_pilot():
    metadata = release_metadata(True)
    index = collection_index('cdec-swe-release', [], True)
    assert metadata == {'processingVersion': 'hydro-ops-cdec-swe-v1-gzip6'}
    assert index['schema'] == 'hydro-ops-observation-index-v1'
    assert 'pilot' not in index
    assert index['releaseId'] == 'cdec-swe-release'
    assert release_metadata(False)['pilot'] is True
    assert collection_index('test', [], False)['pilot'] is True


def record(value='25.4', stamp='2024-02-29T00:00:00-08:00'):
    return {'raw_value': '1', 'swe_mm': value, 'value_status': 'valid',
            'timestamp_pst': stamp, 'station_id': 'BCH', 'sensor_number': '3',
            'duration_code': 'D', 'data_flag': 'r'}


def test_display_policy():
    assert display_value(record('0'))[0] == '0'
    assert display_value(record('-1')) == ('', 'negative_swe')
    assert display_value(record('')) == ('', 'missing')
    assert display_value(dict(record(), raw_value='-9999')) == ('', 'missing')
    with pytest.raises(ValueError):
        display_value(record('nan'))


def test_partition_preserves_leap_day_flags_and_source():
    rows = [record(), record(stamp='2024-10-01T00:00:00-08:00'),
            record(stamp='2023-10-01T00:00:00-08:00')]
    result = export_rows(SimpleNamespace(rows=rows), 'BCH', '3', 2024)
    assert len(result) == 2
    assert result[-1]['timestamp_pst'].startswith('2024-02-29')
    assert result[-1]['data_flag'] == 'r'
    assert 'swe_display_mm' not in rows[0]


def test_reject_duplicates_and_dst():
    with pytest.raises(ValueError, match='Duplicate'):
        export_rows(SimpleNamespace(rows=[record(), record()]), 'BCH', '3', 2024)
    with pytest.raises(ValueError, match='Fixed PST'):
        export_rows(SimpleNamespace(rows=[record(stamp='2024-07-01T00:00:00-07:00')]),
                    'BCH', '3', 2024)


def test_v2_contract():
    document = {'variables': {'swe': {'validRange': {}}}, 'time': {}, 'quality': {},
                'assets': [{'seriesId': 'cdec:BCH:swe:3:D', 'featureId': 'BCH', 'sensor': '3', 'resolution': 'daily'}] * 2,
                'publication': {}, 'provenance': {}}
    refine_contract(document)
    assert document['variables']['swe']['packing']['scale'] == 1
    assert document['variables']['swe']['validationAlreadyApplied'] is True
    assert document['time']['plotColumn'] == 'timestamp_pst'
    assert document['quality']['providerFlagDictionary']['o']['exclude'] is False
    assert document['quality']['displayStatusDictionary']['negative_swe']['exclude'] is True
    assert len(document['series']) == 1
    assert document['seriesKeyBindings']['provider']['constant'] == 'cdec'
    assert document['seriesKeyBindings']['duration_code']['constant'] == 'D'
    assert document['publication']['latestPointer']['enabled'] is False


@pytest.mark.parametrize('value,status,expected', [
    ('0', 'retained_provider_flags_uninterpreted', 0),
    ('25.4', 'retained_provider_flags_uninterpreted', 1),
    ('', 'negative_swe', None), ('', 'missing', None)])
def test_plot_expectations(value, status, expected):
    row = dict(record(stamp='2023-12-07T00:00:00-08:00'),
               observation_timestamp_pst='2023-12-06T00:00:00-08:00',
               swe_display_mm=value, display_status=status)
    result = plot_expectation(row)
    assert result['displayedReportingDate'] == '2023-12-07'
    assert result['plotCoordinate'] == row['timestamp_pst']
    assert result['valueInches'] == expected
    assert result['excluded'] == (expected is None)


def test_monthly_rows_remain_native_and_layer_specific():
    rows = [dict(record(stamp='2024-04-01T00:00:00-08:00'), duration_code='M')]
    result = export_rows(SimpleNamespace(rows=rows), 'BCH', '3', 2024, 'M')
    assert len(result) == 1
    assert result[0]['swe_mm'] == '25.4'
    with pytest.raises(ValueError, match='Mixed'):
        export_rows(SimpleNamespace(rows=rows), 'BCH', '3', 2024, 'D')


def test_layer_readme_notes():
    assert 'daily calculation' not in layer_notes('snow_course').lower()
    assert 'monthly' not in layer_notes('snow_pillow').lower()
    assert 'not monthly means' in layer_notes('snow_course')
    with pytest.raises(ValueError):
        layer_notes('unknown')


def test_full_history_partition_years():
    assert water_years([record(stamp='1910-04-01T00:00:00-08:00'),
                        record(stamp='2026-10-01T00:00:00-08:00'),
                        record(stamp='2026-09-30T00:00:00-08:00')]) == [1910, 2026, 2027]


def test_full_history_export_preserves_cross_year_records():
    rows = [record(stamp='2026-10-01T00:00:00-08:00'),
            record(stamp='1910-04-01T00:00:00-08:00')]
    result = export_rows(SimpleNamespace(rows=rows), 'BCH', '3', None)
    assert len(result) == 2
    assert result[0]['timestamp_pst'].startswith('1910')
    assert result[-1]['timestamp_pst'].startswith('2026')
