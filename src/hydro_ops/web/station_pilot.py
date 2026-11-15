"""Faithful CDEC SWE exports for bounded pilots and full-history publications."""
import csv
import gzip
import io
import json
import math
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.observations.station_store import read_station
from hydro_ops.observations.swe_policy import PAIRS, load_policy

from .globus_transfer import atomic_json, digest


def release_metadata(full_history):
    """Keep experimental labels out of full-history publication contracts."""
    return ({'processingVersion': 'hydro-ops-cdec-swe-v1-gzip6'} if full_history else
            {'processingVersion': 'hydro-ops-swe-two-layer-pilot-v6', 'pilot': True})


def collection_index(release_id, layers, full_history):
    return {'schema': ('hydro-ops-observation-index-v1' if full_history else
                       'hydro-ops-observation-pilot-index-v1'),
            **({} if full_history else {'pilot': True}),
            'releaseId': release_id, 'layers': layers,
            'sharedStationPolicy': 'Separate layers and series; never combine instrument records'}


def refine_contract(document):
    """Add explicit reader semantics without changing scientific records."""
    document['processingVersion'] = 'hydro-ops-swe-pilot-v4'
    variable = document['variables']['swe']
    variable.update(packing={'scale': 1, 'offset': 0}, validationAlreadyApplied=True,
                    authoritativePlotColumn='swe_display_mm')
    variable['validRange']['stage'] = 'decoded-physical-value'
    variable['processingStages'] = [
        {'input': 'value_inches', 'output': 'swe_mm', 'scale': 25.4, 'offset': 0,
         'inputUnit': 'in', 'outputUnit': 'mm', 'alreadyApplied': True},
        {'input': 'swe_mm', 'output': 'swe_display_mm', 'operation': 'mask-missing-and-negative',
         'alreadyApplied': True, 'preserveZero': True}]
    document['time'].update(plotColumn='timestamp_pst',
        displayCalendarDatePolicy='Preserve YYYY-MM-DD of timestamp_pst; never shift to browser local timezone',
        intervalUsePolicy='Provider evidence required before hourly alignment, interval calculations or acquisition-time interpretation',
        plottingPolicy='Use provider reporting labels, intentionally preserving published chronology; not acquisition instants',
        observationTimePolicy='Retain for inspection only; never silently substitute or deduplicate by observation time',
        timezoneEvidence={'status': 'not-independently-verified-for-daily-fields',
            'basis': 'Existing archive normalization uses fixed PST; provider daily date labels have no explicit offset',
            'caveat': 'No definitive no-DST statement for these daily fields was established; do not infer UTC daily intervals'})
    document['quality']['displayStatusDictionary'] = {
        'missing': {'meaning': 'Missing source value or sentinel', 'exclude': True},
        'negative_swe': {'meaning': 'Negative SWE rejected by producer display policy', 'exclude': True},
        'retained_provider_flags_uninterpreted': {'meaning': 'Passed producer numeric checks; provider flag does not determine display eligibility',
                                                 'exclude': False}}
    document['quality']['unknownDisplayStatus'] = 'reject-record-and-report-contract-error'
    document['quality']['providerFlagDictionary'] = {
        flag: {'meaning': meaning, 'exclude': False, 'policy': 'retain; not a guarantee of accuracy'}
        for flag, meaning in {'': 'No Flag', ' ': 'No Flag', 'r': 'Revised', 'e': 'Estimated', 'o': 'Calibration offset applied'}.items()}
    document['quality']['providerFlagLookup'] = 'Exact raw token; empty string and single space both represent blank; preserve raw field'
    document['quality']['providerFlagSource'] = 'https://cdec.water.ca.gov/reportapp/javareports?name=FlagList'
    document['quality']['providerFlagDefinitionsCheckedOn'] = '2026-11-14'
    document['quality']['unknownProviderFlag'] = 'retain verbatim and mark unrecognized; never infer good quality'
    document['quality']['statusCompatibilityNote'] = 'Legacy display-status token retained; provider definitions now supplied separately, without flag-based exclusion'
    document['series'] = list({a['seriesId']: {
        'id': a['seriesId'], 'featureId': a['featureId'], 'variable': 'swe',
        'sensor': a['sensor'], 'sensorColumn': 'sensor_number', 'resolution': a['resolution'],
        'label': f"{a['featureId']} SWE — CDEC sensor {a['sensor']}"}
        for a in document['assets']}.values())
    document['seriesKey'] = ['provider', 'station_id', 'sensor_number', 'duration_code']
    document['seriesKeyBindings'] = {
        'provider': {'constant': 'cdec'},
        'station_id': {'column': 'station_id', 'matchesSeriesField': 'featureId'},
        'sensor_number': {'column': 'sensor_number', 'matchesSeriesField': 'sensor'},
        'duration_code': {'column': 'duration_code', 'constant': 'D'}}
    document['featureAttributes'] = {
        'station_id': {'type': 'string', 'role': 'stable-feature-id'},
        'elevation_raw': {'type': 'string', 'meaning': 'Verbatim provider elevation'},
        'elevation': {'type': 'number-or-null', 'unit': 'ft', 'verticalDatum': 'unknown'},
        'name': {'type': 'string'}, 'basin': {'type': 'string'}, 'operator': {'type': 'string'}}
    document['publication'].update(
        correctionPolicy='Corrections create a new local release/revision; never overwrite an accepted local release',
        deploymentOrder='Publish new release assets before exposing its entry manifest',
        latestPointer={'enabled': False, 'semantics': 'If offered, a mutable pointer to the current release, not immutable content'},
        retentionPolicy='Accepted local releases are not overwritten; remote historical retention is not required by this policy')
    document['provenance'].update(pathRoot='hydro-ops-project-root', downloadable=False,
        classificationArtifact={'href': 'evidence/collection-policy.json', 'downloadable': True})


def plot_expectation(row):
    """Expected reader output; no browser-local date conversion or gap filling."""
    status = row['display_status']
    if status not in ('missing', 'negative_swe', 'retained_provider_flags_uninterpreted'):
        raise ValueError('Unknown display status')
    excluded = status in ('missing', 'negative_swe')
    value = None if excluded else float(row['swe_display_mm'])
    return {'plotCoordinate': row['timestamp_pst'],
            'displayedReportingDate': row['timestamp_pst'][:10],
            'valueMm': value, 'valueInches': value / 25.4 if value is not None else None,
            'excluded': excluded, 'exclusionReason': status if excluded else None,
            'missingRendering': 'gap' if excluded else None}


def display_value(row):
    """Preserve zeros/flags; mask missing and negative SWE without altering source."""
    raw, value = row['raw_value'].strip(), row['swe_mm']
    if raw == '-9999' or not value or row['value_status'].startswith('missing'):
        return '', 'missing'
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Nonfinite normalized SWE')
    if number < 0:
        return '', 'negative_swe'
    return value, 'retained_provider_flags_uninterpreted'


def export_rows(table, station, sensor, year, duration='D'):
    start, end = (f'{year-1}-10-01', f'{year}-10-01') if year is not None else ('', '9999-12-31')
    rows, keys = [], set()
    for original in table.rows:
        stamp = datetime.fromisoformat(original['timestamp_pst'])
        if stamp.utcoffset() != timedelta(hours=-8):
            raise ValueError('Fixed PST required')
        if not start <= original['timestamp_pst'][:10] < end:
            continue
        if (original['station_id'] != station or original['sensor_number'] != sensor
                or original['duration_code'] != duration):
            raise ValueError('Mixed source series')
        key = original['timestamp_pst']
        if key in keys:
            raise ValueError('Duplicate station/sensor/time')
        keys.add(key)
        value, status = display_value(original)
        rows.append(dict(original, swe_display_mm=value, display_status=status))
    return sorted(rows, key=lambda r: r['timestamp_pst'])


def water_years(rows):
    """Partitions containing actual provider records, including partial years."""
    return sorted({int(r['timestamp_pst'][:4]) + (int(r['timestamp_pst'][5:7]) >= 10) for r in rows})


def layer_notes(method):
    if method == 'snow_course':
        return ('Monthly Snow Course values are native surveys/reports, not monthly means; missing months are never zero-filled.\n'
                'Provider monthly labels are reporting coordinates, not averaging intervals or verified acquisition instants.\n'
                'Fixed-PST representation is archive normalization; actual survey timestamps are retained separately.\n')
    if method == 'snow_pillow':
        return ('Daily Snow Pillow values are provider daily records; the exact daily calculation remains unverified.\n'
                'Fixed-PST provider semantics remain unverified; reporting dates are intentional plotting coordinates.\n')
    raise ValueError('Unknown instrument layer')


def build(root, output, stations=('BCH', 'GKS', 'MHP'), years=(2024, 2025), method='snow_pillow', release_id=None):
    sensor_number, resolution = PAIRS[method]
    duration = 'D' if resolution == 'daily' else 'M'
    root, output = Path(root).resolve(), Path(output).resolve()
    release_id = release_id or output.name
    revision = f'{release_id}:{method}'
    output.relative_to(root / 'web/publications/observations')
    output.mkdir(parents=True, exist_ok=False)
    base = root / 'observations/processed/stations/v1'
    catalog = json.loads((base / 'catalog.json').read_text())
    allowed, policy = load_policy(root)
    assets, features, provenance, cases = [], {}, [], {}
    for station in stations:
        if allowed.get((station, sensor_number, resolution)) != method:
            raise ValueError('Station must be eligible for the requested CDEC instrument layer')
        for sensor in (sensor_number,):
            matches = [r for r in catalog['entries'] if r['provider'] == 'cdec'
                and r['station_id'] == station and r['product'] == 'snow_water_equivalent'
                and r['sensor'] == sensor and r['resolution'] == resolution]
            if len(matches) != 1:
                raise ValueError('Unique accepted series required')
            entry = matches[0]
            source = (base / entry['path']).resolve()
            source.relative_to(base)
            checksum = digest(source)
            if checksum != entry['sha256']:
                raise ValueError('Station differs from catalog snapshot')
            table = read_station(source)
            if not table.rows:
                raise ValueError('Empty source series needs explicit review before publication')
            if table.metadata['units_si'] != 'mm' or table.metadata['timezone'] != 'fixed PST (UTC-08:00)':
                raise ValueError('Unexpected SWE units/time semantics')
            meta = table.metadata['auxiliary']['station.json']
            lon, lat = meta['longitude'], meta['latitude']
            if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError('Valid station coordinates required')
            feature = {'type': 'Feature', 'id': station,
                'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': {'station_id': station, 'name': meta['station_name'],
                    'elevation_raw': meta['elevation_raw'], 'basin': meta['river_basin'],
                    'operator': meta['operator'], 'measurement_method': method,
                    'methodEvidence': policy, 'metadataUrl': meta['source_url']}}
            elevation = re.fullmatch(r'\s*([+-]?[\d.]+)\s*ft\s*', meta['elevation_raw'])
            feature['properties']['elevation'] = float(elevation[1]) if elevation else None
            if station in features and features[station] != feature:
                raise ValueError('Sensor metadata disagree on station geometry')
            features[station] = feature
            for year in ((None,) if years is None else years):
                rows = export_rows(table, station, sensor, year, duration)
                if not rows:
                    raise ValueError('Pilot series/year has no records')
                partition_name = f'WY{year}' if year is not None else 'history'
                relative = f'data/{station}/sensor-{sensor}/{partition_name}.csv.gz'
                path = output / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                fields = table.fields + ['swe_display_mm', 'display_status']
                handle = io.StringIO(newline='')
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
                raw_csv = handle.getvalue().encode('utf-8')
                path.write_bytes(gzip.compress(raw_csv, compresslevel=6, mtime=0))
                decoded = gzip.decompress(path.read_bytes())
                if decoded != raw_csv or list(csv.DictReader(io.StringIO(decoded.decode('utf-8'), newline=''))) != rows:
                    raise ValueError('Gzipped CSV roundtrip mismatch')
                observed = {r['timestamp_pst'][:10] for r in rows}
                first, end = ((date(year-1, 10, 1), date(year, 10, 1)) if year is not None else
                              (date.fromisoformat(rows[0]['timestamp_pst'][:10]),
                               date.fromisoformat(rows[-1]['timestamp_pst'][:10]) + timedelta(days=1)))
                absent = [str(first + timedelta(days=i)) for i in range((end-first).days)
                          if str(first + timedelta(days=i)) not in observed]
                asset = {'id': f'{station}-swe-{sensor}-{resolution}-{partition_name}', 'href': relative,
                    'seriesId': f'cdec:{station}:swe:{sensor}:{duration}',
                    'featureId': station, 'sensor': sensor, 'variables': ['swe'], 'resolution': resolution,
                    'mediaType': 'text/csv', 'fileCompression': 'gzip', 'layout': 'wide',
                    'featureIdLocation': 'column:station_id', 'sha256': digest(path), 'bytes': path.stat().st_size,
                    'partition': {'kind': 'water-year' if year is not None else 'full-station-history',
                                  'start': f'{first}T00:00:00-08:00',
                                  'end': f'{end}T00:00:00-08:00', 'closure': '[start,end)',
                                  'meaning': 'Reporting-label selection bounds, not measurement intervals'},
                    'coverage': {'firstTimestamp': rows[0]['timestamp_pst'], 'lastTimestamp': rows[-1]['timestamp_pst'],
                        'rows': len(rows), 'absentProviderDates': absent,
                        'displayStatusCounts': dict(Counter(r['display_status'] for r in rows)),
                        'providerFlagCounts': dict(Counter(r['data_flag'] for r in rows))},
                    'columns': {f: {'type': 'string'} for f in fields}}
                if duration == 'M':
                    asset['coverage'].pop('absentProviderDates')
                    asset['coverage']['samplingPolicy'] = 'Seasonal provider monthly reporting; absent months are not inferred zeros or failed daily samples'
                for f in ('swe_mm', 'swe_display_mm', 'value_inches'):
                    asset['columns'][f] = {'type': 'number-or-empty'}
                assets.append(asset)
                for row in rows:
                    kind = ('zero' if row['swe_display_mm'] and float(row['swe_display_mm']) == 0
                            else row['display_status'])
                    cases.setdefault(kind, {'asset': relative, 'record': row,
                        'expectedInches': float(row['swe_display_mm'])/25.4 if row['swe_display_mm'] else None})
                    tags = [f"provider_flag:{row['data_flag']}"]
                    if row['timestamp_pst'][:10] != row['observation_timestamp_pst'][:10]:
                        tags.append('different_reporting_observation_dates')
                    if row['timestamp_pst'][5:10] in ('02-29', '09-30', '10-01'):
                        tags.append('calendar:' + row['timestamp_pst'][5:10])
                    for tag in tags:
                        cases.setdefault(tag, {'asset': relative, 'record': row})
            if digest(source) != checksum:
                raise ValueError('Station changed during export')
            receipt = table.metadata.get('archive_receipt', {})
            provenance.append({'path': str(source.relative_to(root)), 'sha256': checksum,
                'sourceRetrievalTime': receipt.get('retrieved_utc'),
                'retrievalTimeScope': 'Archive receipt, not a per-record retrieval guarantee',
                'sourceRequestUrl': receipt.get('request_url'),
                'stationMetadataRetrievedAt': meta.get('retrieved_utc'),
                'downloadable': False})
    atomic_json(output / 'features.geojson', {'type': 'FeatureCollection', 'features': list(features.values())})
    document = {'convention': 'hydromet-feature-timeseries-proposal', 'conventionVersion': '0.1-draft',
        'id': 'hydro-ops-cdec-swe-pilot', 'title': 'CDEC Snow Pillow SWE pilot', 'pilot': True,
        'type': 'feature-associated-time-series', 'revision': revision, 'releaseId': release_id,
        'createdAt': datetime.now(UTC).isoformat(), 'processingVersion': 'hydro-ops-swe-pilot-v1',
        'producer': {'name': 'CW3E Hydro Ops', 'contact': 'https://cw3e.ucsd.edu/'},
        'attribution': 'California DWR / CDEC and station operating agencies',
        'reuseTerms': {'source': 'https://data.ca.gov/@california-department-of-water-resources/california-snow-data',
                       'statement': 'DWR source dataset states no restrictions on public use; retain attribution.'},
        'source': {'kind': 'observation', 'network': 'CDEC', 'sensorCodes': {'3': 'SNOW, WATER CONTENT',
                   '82': 'SNOW, WATER CONTENT(REVISED)'}, 'sensorPrecedence': 'None; separate selectable series'},
        'geometry': {'href': 'features.geojson', 'crs': 'OGC:CRS84', 'coordinateOrder': 'longitude,latitude',
            'featureIdField': 'station_id', 'featureIdType': 'string', 'datasetVersion': revision,
            'caveat': 'Reported station coordinates used without datum transformation; sensor footprint/relocations not resolved.'},
        'variables': {'swe': {'label': 'Snow water equivalent', 'shortLabel': 'SWE', 'unit': 'mm',
            'quantity': 'snow_water_equivalent_depth', 'column': 'swe_display_mm', 'rawNormalizedColumn': 'swe_mm',
            'nativeValueColumn': 'value_inches', 'nativeUnit': 'in', 'conversion': 'mm = inches * 25.4',
            'spatialSupport': 'Point station observation, not basin mean',
            'temporalStatistic': 'Provider daily SWE product; exact daily calculation unverified, not assumed mean',
            'validRange': {'minimum': 0, 'includeMinimum': True, 'maximum': None, 'unit': 'mm',
                           'onViolation': 'mask-and-retain-reason'}, 'missingTokens': [''],
            'qualityColumn': 'display_status'}},
        'time': {'column': 'timestamp_pst', 'utcColumn': 'timestamp_utc',
            'observationTimeColumn': 'observation_timestamp_pst', 'format': 'ISO-8601 with explicit offset',
            'calendar': 'gregorian', 'zone': 'fixed PST', 'utcOffset': '-08:00', 'daylightSaving': False,
            'meaning': 'Provider reporting label; observation timestamp retained separately; no daily interval inferred',
            'expectedCadence': 'P1D', 'aggregation': 'none; exported provider daily values'},
        'csv': {'textEncoding': 'UTF-8', 'delimiter': ',', 'quoteCharacter': '"', 'header': True,
            'decimalSeparator': '.', 'missingTokens': [''], 'rawSentinel': '-9999',
            'recordKey': ['station_id', 'sensor_number', 'duration_code', 'timestamp_pst'],
            'rowOrder': 'ascending timestamp_pst', 'duplicateRecords': 'reject', 'connectAcrossMissing': False},
        'quality': {'providerColumn': 'data_flag', 'sourceStatusColumn': 'value_status',
            'unknownProviderFlag': 'Retain verbatim; not interpreted as reviewed or good',
            'providerFlagExclusions': [], 'displayRules': 'Mask missing/sentinel and negative SWE; preserve valid zero; no maximum cutoff',
            'interpolation': 'none', 'scientificReview': 'Pending; archive validation proves faithful export, not observation accuracy'},
        'assets': assets, 'publication': {'immutableLocalRelease': True, 'updatePolicy': 'Pilot snapshot; not operational'},
        'provenance': {'stationSources': provenance}}
    refine_contract(document)
    document.pop('pilot', None)
    document.update(release_metadata(years is None))
    document['publication']['updatePolicy'] = 'Accepted archive snapshot; routine publication not enabled'
    document['publication']['assetPartitioning'] = 'full-station-history' if years is None else 'water-year'
    document['id'] = f'hydro-ops-cdec-{method.replace("_", "-")}-swe'
    document['title'] = 'CDEC Snow Pillows — Daily SWE' if duration == 'D' else 'CDEC Snow Courses — Monthly SWE'
    document['source']['sensorCodes'] = {sensor_number: 'SNOW, WATER CONTENT(REVISED)' if duration == 'D' else 'SNOW, WATER CONTENT'}
    document['source']['measurementMethod'] = method
    document['source']['sensorPrecedence'] = 'Only the approved station/sensor/duration combination is published in this layer'
    document['seriesKeyBindings']['duration_code']['constant'] = duration
    if duration == 'M':
        document['variables']['swe']['spatialSupport'] = 'Snow course survey represented by a station point; not basin mean or pillow measurement'
        document['variables']['swe']['temporalStatistic'] = 'Native monthly Snow Course survey/report; not monthly mean, accumulation or daily-data reduction'
        document['time'].update(expectedCadence='P1M', regularSampling=False,
            aggregation='none; provider monthly survey values',
            meaning='Provider monthly reporting label; actual observation timestamp retained separately; no monthly interval inferred',
            plottingPolicy='Use provider reporting labels; never substitute survey dates or browser-local dates',
            timezoneEvidence={'status': 'archive-normalization-only',
                'basis': 'Existing fixed-PST representation of provider labels; not a verified acquisition instant'},
            intervalUsePolicy='Do not interpret monthly labels as averaging intervals or acquisition instants')
    document['provenance']['collectionPolicy'] = policy
    (output / 'evidence').mkdir()
    atomic_json(output / 'evidence/collection-policy.json', policy)
    for source in policy['sources']:
        (output / 'evidence' / Path(source['path']).name).write_bytes((root/source['path']).read_bytes())
    atomic_json(output / 'collection.json', document)
    (output / 'README.txt').write_text(
        f'{document["title"]}\nRelease: {release_id}\nEntry point: collection.json\n'
        'Upload this entire directory, preserving relative paths. No NetCDF source files are needed.\n'
        'CSV assets are gzip files; decompress before parsing. Metadata bytes/checksums refer to compressed files.\n'
        'Plot swe_display_mm against timestamp_pst. Values are already mm and masked; do not convert from inches again.\n'
        'Display inches by dividing accepted mm by 25.4; preserve blanks and zeros.\n'
        'Preserve the reporting calendar date; never shift it to the browser local timezone.\n'
        'Corrections create a new local release; accepted prior releases are not overwritten.\n'
        'Publish data before the entry manifest. A future latest URL would be a mutable pointer, not immutable content.\n'
        f'Only approved {method} stations with sensor {sensor_number}/{resolution} are included.\n'
        + layer_notes(method) +
        'Known provider flags are documented, not used as assurance of observation accuracy.\n'
        'Local checks validate export fidelity, not scientific accuracy or browser/HTTPS integration.\n'
        'Source paths are nondownloadable references relative to the Hydro Ops project root.\n', encoding='utf-8')
    for case in cases.values():
        case['expectedPlot'] = plot_expectation(case['record'])
    atomic_json(output / 'reference-records.json', {'realRecords': cases,
        'syntheticConversionCheck': {'mm': 25.4, 'inches': 1, 'notAnObservation': True}})
    for row in provenance:
        if digest(root / row['path']) != row['sha256']:
            raise ValueError('Input changed before acceptance')
    files = [p for p in sorted(output.rglob('*')) if p.is_file()]
    atomic_json(output / 'acceptance.json', {'status': 'passed', 'scope': 'Local export/readback only; scientific/browser/HTTPS review pending',
        'stations': len(features), 'assets': len(assets), 'records': sum(a['coverage']['rows'] for a in assets),
        'files': [{'path': str(p.relative_to(output)), 'sha256': digest(p), 'bytes': p.stat().st_size} for p in files]})
    return output


def build_two_layers(root, output, *, full_history=False):
    """Two independent feature collections; shared IDs never merge observations."""
    root, output = Path(root).resolve(), Path(output).resolve()
    output.relative_to(root/'web/publications/observations')
    output.mkdir(parents=True, exist_ok=False)
    layers = []
    groups = [('snow_pillow', ('BCH', 'CAP', 'BCB')), ('snow_course', ('3LK', 'CAP', 'BCB'))]
    if full_history:
        allowed, _ = load_policy(root)
        groups = [(method, sorted(s for (s, _, _), m in allowed.items() if m == method))
                  for method in ('snow_pillow', 'snow_course')]
    for method, stations in groups:
        folder = build(root, output/method, stations=stations, method=method, release_id=output.name,
                       years=None if full_history else (2024, 2025))
        doc = json.loads((folder/'collection.json').read_text())
        accepted = json.loads((folder/'acceptance.json').read_text())
        if accepted['status'] != 'passed':
            raise ValueError('Layer acceptance required')
        if full_history:
            catalog = json.loads((root/'observations/processed/stations/v1/catalog.json').read_text())
            sensor, resolution = PAIRS[method]
            expected = [e for e in catalog['entries'] if e['provider'] == 'cdec'
                        and e['product'] == 'snow_water_equivalent' and str(e['sensor']) == sensor
                        and e['resolution'] == resolution and e['station_id'] in stations]
            if len(expected) != len(stations) or sum(e['rows'] for e in expected) != accepted['records']:
                raise ValueError('Full history station/record completeness mismatch')
        layers.append({'id': doc['id'], 'title': doc['title'], 'href': f'{method}/collection.json',
                       'revision': doc['revision'],
                       'measurementMethod': method, 'stations': accepted['stations'],
                       'records': accepted['records'], 'assets': accepted['assets']})
    atomic_json(output/'catalog.json', collection_index(output.name, layers, full_history))
    (output/'README.txt').write_text(
        f'CDEC SWE two-layer publication\nRelease: {output.name}\nEntry point: catalog.json (index of two collection.json documents).\n'
        'Upload the entire folder preserving relative paths, data before manifests and catalog.\n'
        'snow_pillow: sensor 82/daily; snow_course: sensor 3/monthly. Shared stations appear independently in both.\n'
        + ('One full-history CSV per station per layer, including partial first/latest years.\n' if full_history else 'Both cover WY2024-WY2025.\n') +
        'Preserve reporting labels and missing values; do not aggregate monthly course surveys.\n'
        'Other archived SWE combinations are retained but excluded from publication. No automated transfer or cron enabled.\n', encoding='utf-8')
    atomic_json(output/'acceptance.json', {'status': 'passed', 'layers': layers,
        'scope': 'Local archive-to-CSV fidelity; scientific/browser/HTTPS acceptance separate',
        'files': [{'path': str(p.relative_to(output)), 'sha256': digest(p), 'bytes': p.stat().st_size}
                  for p in sorted(output.rglob('*')) if p.is_file()]})
    return output
