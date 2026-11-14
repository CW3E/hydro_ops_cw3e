"""Scientific and transport contract for the version-3 web integration pilot."""
from datetime import date, timedelta

import numpy as np


def portable_identity(pin):
    return dict(zip(('inode', 'sizeBytes', 'mtimeNanoseconds'), map(str, pin), strict=True))


def decoded_field(variable, index):
    """Honor NetCDF masks/packing; preserve invalid counts before transformations."""
    values = np.ma.asarray(variable[index], dtype='f8').filled(np.nan)
    return values


def physical_values(values, field):
    values = np.asarray(values, dtype=float).copy()
    invalid = np.isfinite(values) & ((values <= 0) if field == 'T2D' else (values < 0))
    values[invalid | ~np.isfinite(values)] = np.nan
    return values, invalid


def variables():
    quality = {
        'decode': 'NetCDF4 automatic masking and unpacking: _FillValue, missing_value, valid_range or valid_min/valid_max, scale_factor/add_offset when present. Nonfinite decoded values are missing; per-asset source attributes are retained.',
        'order': 'Decode/mask, validate physical values, compute statistic/conversion, apply domain mask, classify colors.',
        'precipitation': 'Finite RAINRATE >= 0 only; negative values become missing before depth conversion AND before monthly percentile inversion. Never clamp negative precipitation to zero.',
        'temperature': 'Finite T2D > 0 K; no additional climatological clipping. Values outside legend boundaries use endpoint colors.',
        'soil': 'All four decoded SOIL_M layers finite and in [0,1]; otherwise the column is missing.',
        'snow': 'Finite SNEQV >= 0; values <= 1 kg m-2 are valid snow estimates but their ranks are intentionally suppressed.',
        'domain': 'Forcing products use subset_forcing_mask. Model products use model_mask intersected with subset_forcing_mask. The masks are deliberately different.',
        'transparency': 'All fully transparent pixels are #00000000. Color alone cannot distinguish outside, missing, suppressed and valid hidden values. Consult qualityCounts; numerical interrogation requires a companion dataset.',
    }
    rank_method = {
        'quantity': 'non-exceedance percentile rank', 'unit': '1', 'encodedScale': '0–100',
        'method': 'Invert 0.5,1.5,...,99.5 threshold table; tied thresholds use mean ranks; linear interpolation; tails clipped to 0.5/99.5. Not an exact empirical CDF.',
        'quantileEstimator': 'Hyndman–Fan type 8 (median_unbiased)',
        'referencePeriod': ['1979-10-01', '2025-09-30'],
        'missingSamples': 'Excluded; never replaced with zeros.',
    }
    daily = rank_method | {
        'window': '31 actual calendar days centered on each reference date (15 before, center, 15 after); 15-day history padding on both reference-period boundaries.',
        'leapDay': 'February 29 thresholds average February 28 and March 1 thresholds. Leap observations remain in neighboring windows; Feb29 sample count is -1 (interpolated).',
        'minimumSamples': 1, 'sampleCaveat': 'Daily tables record actual sample counts. Minimum one is the existing estimator configuration, not a completeness guarantee.',
    }
    monthly = rank_method | {'window': 'Same calendar month in 46 water years; no moving window.',
                            'minimumSamples': 30, 'maximumYears': 46}
    definitions = {
        'temperature': {'label': 'Daily mean air temperature', 'shortLabel': 'Daily T', 'quantity': 'air temperature',
            'unit': 'Cel', 'sourceFields': ['T2D'], 'sourceUnits': ['K'],
            'resolution': 'daily', 'aggregation': 'Time mean over the UTC day',
            'processing': 'Daily mean T2D minus 273.15; not Tmin/Tmax midpoint and not a snapshot.'},
        'precipitation': {'label': 'Daily precipitation depth', 'shortLabel': 'Daily P', 'quantity': 'precipitation depth',
            'unit': 'mm', 'sourceFields': ['RAINRATE'], 'sourceUnits': ['kg m-2 s-1'],
            'resolution': 'daily', 'aggregation': 'Accumulation over the UTC day',
            'processing': 'Mean RAINRATE multiplied by time_bounds duration in seconds (86400 for these days); 1 kg m-2 water = 1 mm. Scientific forcing archives continue to store a mean rate.'},
        'soil_water_percentile': {'label': 'Daily column soil-water percentile rank', 'shortLabel': 'Daily SM %-ile', **daily,
            'sourceFields': ['SOIL_M'], 'sourceUnits': ['m3 m-3'], 'resolution': 'daily',
            'aggregation': 'Percentile rank of depth-weighted daily mean column storage; not mean of hourly ranks.',
            'processing': 'SOIL_WATER [mm] = 1000 * sum(daily_mean_SOIL_M[layer] * thickness_m[layer]); thicknesses [0.1,0.3,0.6,1.0] m, total depth 2 m.'},
        'swe_percentile': {'label': 'Daily mean SWE percentile rank', 'shortLabel': 'Daily SWE %-ile', **daily,
            'sourceFields': ['SNEQV'], 'sourceUnits': ['kg m-2'], 'resolution': 'daily',
            'aggregation': 'Percentile rank of daily mean SWE; not mean of hourly ranks.',
            'processing': 'Rank only where daily mean SWE > 1 kg m-2 (1 mm). Zeros remain in the reference distribution.'},
        'temperature_percentile': {'label': 'Monthly mean temperature percentile rank', 'shortLabel': 'Monthly T %-ile', **monthly,
            'sourceFields': ['T2D'], 'sourceUnits': ['K'], 'resolution': 'monthly',
            'aggregation': 'Percentile rank of calendar-month mean T2D, not extrema or daily ranks.',
            'processing': 'Compare the monthly mean to the same-calendar-month climatology.'},
        'precipitation_percentile': {'label': 'Monthly mean precipitation-rate percentile rank', 'shortLabel': 'Monthly P %-ile', **monthly,
            'sourceFields': ['RAINRATE'], 'sourceUnits': ['kg m-2 s-1'], 'resolution': 'monthly',
            'aggregation': 'Percentile rank of calendar-month mean RAINRATE; not daily ranks.',
            'processing': 'Rank mean rates, not depths. Fixed-length month totals have identical ordering; leap versus non-leap February depth ranks can differ.'},
    }
    for definition in definitions.values():
        if definition.get('quantity') == 'non-exceedance percentile rank':
            definition['numericScale'] = {'minimum': 0, 'maximum': 100, 'legendThresholdScale': [0, 100],
                'multiplyBy100ForDisplay': False, 'displayUnitConversion': 'none', 'unit': '1'}
    return {'conventionVersion': '0.4', 'variables': definitions, 'qualityRules': quality,
            'unitVocabulary': {'temperature': 'Cel', 'temperatureAliases': ['degC', 'degree_Celsius'],
                'percentileRank': '1', 'percentileDisplayLabel': '%',
                'percentileNote': 'Values and legend thresholds already use 0–100; never multiply by 100.'},
            'aggregateCompleteness': {
                'daily': {'requiredSamples': 24, 'sampleInterval': 'PT1H', 'weighting': 'equal hourly samples',
                    'endpoints': '01 UTC through following 00 UTC', 'incompleteInterval': 'reject'},
                'monthly': {'requiredSamples': 'all calendar days (28,29,30,31)',
                    'weighting': 'equal complete 24-hour daily means', 'incompleteInterval': 'reject'},
                'forcingMissingCells': 'Missing/nonfinite inputs propagate; no temporal skipna or zero fill.',
                'publisherChecks': 'Check aggregate sample count and time bounds; consume archived aggregates, not recompute hourly history.',
                'distinctFromClimatology': 'Climatology minimumSamples governs reference-distribution availability, not aggregate completeness.'},
            'timeConvention': 'UTC [start,end), labeled by start date; daily means/accumulations use interval-end hourly records 01–00 UTC, not a simple grouping of timestamp dates.',
            'displayUnits': 'Legend display conversions are suggestions only. Native quantity/unit definitions govern conversions; percentile ranks are dimensionless and never converted to precipitation or temperature units.'}


def coverage(rows, requests, cutoff):
    """Describe preferred coverage; absence of an alternate stream is not a gap."""
    groups = {}
    for row in rows:
        key = row['product'], row['variable'], row['resolution']
        groups.setdefault(key, []).append(row)
    output, gaps, incomplete = [], [], []
    for key, wanted in requests.items():
        assets = groups.get(key, [])
        dates = sorted({a['date'] for a in assets})
        output.append({'product': key[0], 'variable': key[1], 'resolution': key[2],
            'firstAvailable': dates[0] if dates else None, 'lastAvailable': dates[-1] if dates else None,
            'availableIntervals': len(dates), 'assetCount': len(assets),
            'streams': {s: {'firstAvailable': min(a['date'] for a in assets if a['stream'] == s),
                            'lastAvailable': max(a['date'] for a in assets if a['stream'] == s),
                            'assetCount': sum(a['stream'] == s for a in assets)}
                        for s in sorted({a['stream'] for a in assets})}})
        for stamp in wanted:
            d = date.fromisoformat(stamp)
            end = ((d.replace(day=28)+timedelta(days=4)).replace(day=1)
                   if key[2] == 'monthly' else d+timedelta(days=1))
            base = {'product': key[0], 'variable': key[1], 'resolution': key[2],
                    'stream': 'preferred-across-streams', 'date': stamp}
            if end > cutoff+timedelta(days=1):
                incomplete.append(base | {'reason': 'interval_not_complete_at_collection_cutoff'})
            elif stamp not in dates:
                gaps.append(base | {'reason': 'no_asset_in_any_stream_for_requested_interval'})
    return {'scope': 'explicit requested pilot samples, not continuous coverage between first and last',
            'gapSemantics': 'No preferred asset across all streams; lack of an individual alternate stream is not a gap.',
            'cutoff': str(cutoff), 'series': output, 'gaps': gaps, 'incompleteIntervals': incomplete}
