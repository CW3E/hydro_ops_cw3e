from datetime import date

import numpy as np
from PIL import Image

from hydro_ops.forcing.web_contract import coverage, physical_values, portable_identity, variables
from hydro_ops.forcing.web_rasters import legends_v3, render_v3, save_indexed_png
from hydro_ops.wrf_hydro.climatology import percentile_rank


def test_short_labels_preserve_full_definitions():
    definitions = variables()['variables']
    assert {name: item['shortLabel'] for name, item in definitions.items()} == {
        'soil_water_percentile': 'Daily SM %-ile', 'swe_percentile': 'Daily SWE %-ile',
        'precipitation': 'Daily P', 'temperature': 'Daily T',
        'precipitation_percentile': 'Monthly P %-ile', 'temperature_percentile': 'Monthly T %-ile'}
    assert all(item['label'] != item['shortLabel'] for item in definitions.values())


def test_quality_categories_and_exact_transparency(tmp_path):
    # outside, missing, invalid, suppressed, unavailable statistic, hidden, visible
    x, y = np.meshgrid(np.arange(8), np.arange(2))
    values = np.array([[50, np.nan, -1, np.nan, np.nan, 50, 90.]])
    args = {'domain_mask': np.array([[False, True, True, True, True, True, True]]),
            'source_valid': np.array([[True, False, False, True, True, True, True]]),
            'invalid': np.array([[False, False, True, False, False, False, False]]),
            'suppressed': np.array([[False, False, False, True, False, False, False]])}
    path = tmp_path/'quality.png'
    report = render_v3(path, values, x, y, [0, 0, 7, 1], [700, 100], legends_v3()['swe_percentile'], **args)
    counts = report['qualityCounts']['sourceCells']
    assert counts['insideDomain'] == 6 and counts['validEstimate'] == 4
    for key in ('outsideDomain', 'missingEstimate', 'invalidEstimate', 'suppressedBySweCutoff',
                'statisticUnavailable', 'validIntentionallyHidden', 'visible'):
        assert counts[key] == 1
        assert report['qualityCounts']['imagePixels'][key] > 0
    rgba = np.asarray(Image.open(path))
    assert (rgba[rgba[..., 3] == 0] == 0).all()
    assert report['visiblePixels'] == np.count_nonzero(rgba[..., 3])
    save_indexed_png(path, tmp_path/'indexed.png')
    assert report['imageStatus'] == 'visible_data'


def test_fully_transparent_does_not_mean_missing(tmp_path):
    x, y = np.meshgrid(np.arange(3), np.arange(2))
    for val, expected in [(0., 'valid_data_not_highlighted'), (np.nan, 'no_displayable_estimates')]:
        report = render_v3(tmp_path/'blank.png', np.full((1, 2), val), x, y, [0, 0, 2, 1], [200, 100],
                           legends_v3()['precipitation'], domain_mask=np.ones((1, 2), bool))
        assert report['visiblePixels'] == 0 and report['imageStatus'] == expected


def test_validation_before_monthly_rank_and_named_identity():
    values, bad = physical_values(np.array([[-1., 0., 1., np.nan]]), 'RAINRATE')
    ranks = percentile_rank(values, np.stack([np.zeros(values.shape), np.ones(values.shape)]), [0.5, 99.5])
    assert bad[0, 0] and np.isnan(ranks[0, 0]) and ranks[0, 1] == 0.5
    pin = portable_identity([2**63+123, 1234, 1794291266000000000])
    assert all(isinstance(v, str) for v in pin.values())
    assert int(pin['inode']) == 2**63+123
    assert variables()['variables']['soil_water_percentile']['unit'] == '1'
    assert 'not mean of hourly ranks' in variables()['variables']['swe_percentile']['aggregation']


def test_coverage_resolution_stream_and_incomplete_month():
    daily = ('forcing', 'temperature', 'daily')
    monthly = ('forcing', 'temperature_percentile', 'monthly')
    rows = [{'product': 'forcing', 'variable': 'temperature', 'resolution': 'daily',
             'date': '2026-11-01', 'stream': 'nrt'}]
    result = coverage(rows, {daily: ['2026-11-01', '2026-11-02'], monthly: ['2026-10-01', '2026-11-01']}, date(2026, 11, 11))
    assert len(result['gaps']) == 2 and len(result['incompleteIntervals']) == 1
    assert result['incompleteIntervals'][0]['date'] == '2026-11-01'
    assert all(g['stream'] == 'preferred-across-streams' and g['resolution'] for g in result['gaps'])
    assert result['series'][0]['streams']['nrt']['assetCount'] == 1
