"""Full-history CDEC FNF pilot: native daily rates and monthly volumes."""
import csv
import gzip
import io
import json
import math
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

from hydro_ops.download.cdec_fnf import PRODUCTS
from hydro_ops.observations.station_store import read_station

from .globus_transfer import atomic_json, digest
from .station_gzip import inventory
from .station_pilot import collection_index

FLAG_SOURCE = 'https://cdec.water.ca.gov/reportapp/javareports?name=FlagList'
# Verified against the official case-sensitive list on 2026-11-15.
FLAG_MEANINGS = {'': 'No Flag', ' ': 'No Flag', 'e': 'Estimated', 'r': 'Revised',
                 'A': 'Precipitation accumulation', 's': 'New shift started',
                 'N': 'Error in data', 't': 'Trace of precipitation'}


def flag_dictionary(assets):
    flags = {flag for a in assets for flag in a['coverage']['providerFlagCounts']}
    return {flag: {'meaning': FLAG_MEANINGS.get(flag, 'Unknown'),
                   'definitionStatus': 'provider-documented' if flag in FLAG_MEANINGS else 'unresolved',
                   'exclude': False, 'source': FLAG_SOURCE if flag in FLAG_MEANINGS else None,
                   'caveat': 'Generic provider definition; applicability to FNF is not established. Retain raw case and value, not assurance of accuracy.'}
            for flag in sorted(flags)}


def gap_references(rows, resolution, asset):
    gaps = []
    for previous, following in pairwise(rows):
        first = date.fromisoformat(previous['period_label'] + ('-01' if resolution == 'monthly' else ''))
        next_date = (first + timedelta(days=1) if resolution == 'daily' else
                     date(first.year + (first.month == 12), first.month % 12 + 1, 1))
        last = date.fromisoformat(following['period_label'] + ('-01' if resolution == 'monthly' else ''))
        if next_date < last:
            gaps.append({'asset': asset, 'precedingRecord': previous, 'followingRecord': following,
                         'firstAbsentPeriod': next_date.isoformat() if resolution == 'daily' else next_date.strftime('%Y-%m'),
                         'expectedPlot': {'breakLine': True, 'insertPresentationGap': True,
                                          'fillValue': None, 'modifyPublishedRecords': False},
                         'meaning': 'Absent reporting periods, not records with explicit missing values'})
    return gaps


def update_evidence(root, report_path):
    if report_path is None:
        return {}
    path = Path(report_path).resolve()
    path.relative_to(root/'observations/work/station-cycles')
    report = json.loads(path.read_text())
    if report['status'] != 'passed' or report['selection'] != 'cdec-fnf':
        raise ValueError('Passed FNF update report required')
    result = {}
    for station in report['stations']:
        receipt_path = path.parent/str(int(station['task_index']))/'publication.json'
        receipt = json.loads(receipt_path.read_text())
        entry = receipt['result']['entry']
        if receipt['status'] != 'passed' or entry['path'] != station['path'] or entry['sha256'] != station['sha256']:
            raise ValueError('Update receipt identity mismatch')
        result[entry['path']] = {'entry': entry, 'receipt': receipt,
                                'reportPath': str(path.relative_to(root)), 'reportSha256': digest(path),
                                'receiptPath': str(receipt_path.relative_to(root)), 'receiptSha256': digest(receipt_path)}
    return result


def verify_catalog_hash(output, report):
    actual = digest(output/'catalog.json')
    listed = [f['sha256'] for f in report['files'] if f['path'] == 'catalog.json']
    if report['publication_catalog_sha256'] != actual or listed != [actual]:
        raise ValueError('Contradictory publication catalog checksums')


def export_rows(table, station, resolution):
    sensor, duration, native, factor, unit = PRODUCTS[resolution]
    rows, keys = [], set()
    for original in table.rows:
        stamp = datetime.fromisoformat(original['timestamp_pst'])
        if stamp.utcoffset() != timedelta(hours=-8):
            raise ValueError('Fixed PST required')
        if (original['station_id'], original['sensor_number'], original['duration_code'],
                original['units'], original['units_si']) != (station, str(sensor), duration, native, unit):
            raise ValueError('Mixed source series or units')
        label = stamp.strftime('%Y-%m-%d' if resolution == 'daily' else '%Y-%m')
        if original['period_label'] != label or label in keys:
            raise ValueError('Duplicate or inconsistent reporting label')
        keys.add(label)
        value = original['value_si']
        if value:
            number = float(value)
            if (not math.isfinite(number) or original['raw_value'].strip() == '-9999'
                    or original['value_status'] not in ('numeric', 'negative')
                    or not math.isclose(number, float(original['value'])*factor, rel_tol=1e-12, abs_tol=1e-12)):
                raise ValueError('Invalid normalized FNF value')
            status = 'negative_retained' if number < 0 else 'retained'
        else:
            if original['value_status'] in ('numeric', 'negative'):
                raise ValueError('Numeric source missing normalized value')
            status = 'missing'
        rows.append(dict(original, fnf_display_si=value, display_status=status))
    return sorted(rows, key=lambda row: row['timestamp_pst'])


def manifest(resolution, release, assets, sources):
    sensor, duration, native, factor, unit = PRODUCTS[resolution]
    variable = 'fnf_flow' if resolution == 'daily' else 'fnf_volume'
    title = 'CDEC Daily Full Natural Flow' if resolution == 'daily' else 'CDEC Monthly Full Natural Flow Volume'
    revision = f'{release}:{resolution}'
    return {
        'convention': 'hydromet-feature-timeseries-proposal', 'conventionVersion': '0.1-draft',
        'id': f'hydro-ops-cdec-fnf-{resolution}', 'title': title, 'pilot': True,
        'description': 'Calculated naturalized flow, not measured regulated discharge. Native provider products; not recomputed from another resolution.',
        'type': 'feature-associated-time-series', 'revision': revision, 'releaseId': release,
        'createdAt': datetime.now(UTC).isoformat(), 'processingVersion': 'hydro-ops-cdec-fnf-pilot-v2-gzip6',
        'producer': {'name': 'CW3E Hydro Ops', 'contact': 'https://cw3e.ucsd.edu/'},
        'attribution': 'California DWR / CDEC and contributing agencies',
        'reuseTerms': {'status': 'not-independently-verified', 'statement': 'Retain provider attribution; no additional license inferred from SWE metadata.'},
        'source': {'kind': 'derived_estimate', 'provider': 'CDEC', 'network': 'CDEC', 'product': 'calculated full natural flow',
                   'sensorCodes': {str(sensor): title}, 'sensorPrecedence': 'Only this sensor/duration; sensor 290 excluded'},
        'geometry': {'href': 'features.geojson', 'crs': 'OGC:CRS84', 'coordinateOrder': 'longitude,latitude',
                     'featureIdField': 'station_id', 'featureIdType': 'string', 'datasetVersion': revision,
                     'caveat': 'Provider station location; not a validated basin outlet or reach match. Historical calculation/location changes retained in station notes.'},
        'featureAttributes': {'station_id': {'type': 'string'}, 'name': {'type': 'string'},
                              'basin': {'type': 'string'}, 'elevation_raw': {'type': 'string'},
                              'historical_notes': {'type': 'array', 'meaning': 'Verbatim provider notes; calculation and location changes may affect comparison'}},
        'variables': {variable: {
            'label': title.removeprefix('CDEC '), 'shortLabel': 'Daily FNF' if resolution == 'daily' else 'Monthly FNF',
            'quantity': 'volume_flow_rate' if resolution == 'daily' else 'water_volume',
            'unit': unit, 'column': 'fnf_display_si', 'rawNormalizedColumn': 'value_si',
            'nativeValueColumn': 'value', 'nativeUnit': native,
            'conversion': f'SI = native * {factor}', 'nativeToSI': factor,
            'packing': {'scale': 1, 'offset': 0}, 'validationAlreadyApplied': True,
            'authoritativePlotColumn': 'fnf_display_si',
            'temporalStatistic': ('Provider daily flow rate; exact averaging interval unverified' if resolution == 'daily'
                                  else 'Provider monthly volume; not a flow rate or sum derived from published daily FNF'),
            'spatialSupport': 'Naturalized basin flow/volume associated with provider station; not a direct point measurement',
            'validRange': {'minimum': None, 'maximum': None, 'unit': unit, 'stage': 'decoded-physical-value',
                           'onViolation': 'reject-nonfinite', 'rationale': 'No defensible numeric cutoff; negative estimates retained for review'},
            'missingTokens': [''], 'qualityColumn': 'display_status'}},
        'time': {'column': 'timestamp_pst', 'plotColumn': 'timestamp_pst', 'utcColumn': 'timestamp_utc',
                 'observationTimeColumn': 'observation_timestamp_pst', 'format': 'ISO-8601 with explicit offset',
                 'observationTimeLabel': 'Provider observation timestamp',
                 'observationTimeMeaning': 'Unverified provider secondary timestamp; not a verified physical measurement date',
                 'observationTimePolicy': 'Retain for inspection; never substitute for reporting date or infer acquisition time',
                 'calendar': 'gregorian', 'zone': 'fixed PST', 'utcOffset': '-08:00', 'daylightSaving': False,
                 'expectedCadence': 'P1D' if duration == 'D' else 'P1M',
                 'meaning': 'Provider reporting label, not a verified interval-end timestamp',
                 'aggregation': 'none; native provider product',
                 'displayCalendarDatePolicy': 'Preserve provider reporting date/month; never shift to browser-local time',
                 'intervalUsePolicy': 'Interval boundaries unverified; no NWM UTC-day alignment or rate/volume conversion inferred',
                 'timezoneEvidence': {'source': 'https://cdec.water.ca.gov/dynamicapp/wsSensorData',
                                      'basis': 'CDEC service documents PST; archive normalizes fixed UTC-08:00'}},
        'csv': {'textEncoding': 'UTF-8', 'delimiter': ',', 'quoteCharacter': '"', 'header': True,
                'decimalSeparator': '.', 'missingTokens': [''], 'rawSentinel': '-9999',
                'recordKey': ['station_id', 'sensor_number', 'duration_code', 'period_label'],
                'rowOrder': 'ascending timestamp_pst', 'duplicateRecords': 'reject', 'connectAcrossMissing': False,
                'absentPeriodPolicy': {'cadence': 'P1D' if duration == 'D' else 'P1M',
                                       'calendar': 'provider reporting dates/months in fixed PST',
                                       'action': 'Insert presentation-only null gap between nonconsecutive periods; never zero-fill',
                                       'connectgapsFalseAloneSufficient': False}},
        'quality': {'providerColumn': 'data_flag', 'sourceStatusColumn': 'value_status',
                    'providerFlagExclusions': [], 'unknownProviderFlag': 'retain verbatim; not assurance of accuracy',
                    'providerFlagDictionary': flag_dictionary(assets),
                    'providerFlagDefinitionsCheckedOn': '2026-11-15',
                    'providerFlagLookup': 'Exact case-sensitive raw token; do not infer unlisted meanings',
                    'displayStatusDictionary': {
                        'retained': {'exclude': False, 'meaning': 'Finite source value; flags retained'},
                        'negative_retained': {'exclude': False, 'meaning': 'Negative estimate retained for review, not clipped'},
                        'missing': {'exclude': True, 'meaning': 'Missing, sentinel or nonnumeric source'}},
                    'unknownDisplayStatus': 'reject-record-and-report-contract-error',
                    'interpolation': 'none', 'scientificReview': 'Export fidelity only; provider estimates not independently validated'},
        'series': [{'id': a['seriesId'], 'featureId': a['featureId'], 'variable': variable,
                    'sensor': str(sensor), 'resolution': resolution} for a in assets],
        'seriesKey': ['provider', 'station_id', 'sensor_number', 'duration_code'],
        'seriesKeyBindings': {'provider': {'constant': 'cdec'},
                              'station_id': {'column': 'station_id', 'matchesSeriesField': 'featureId'},
                              'sensor_number': {'column': 'sensor_number', 'matchesSeriesField': 'sensor'},
                              'duration_code': {'column': 'duration_code', 'constant': duration}},
        'assets': assets, 'publication': {'immutableLocalRelease': True, 'assetPartitioning': 'full-station-history',
                                         'updatePolicy': 'Pilot snapshot; routine publication not enabled',
                                         'deploymentOrder': 'Assets first, collection manifests next, catalog last'},
        'provenance': {'pathRoot': 'hydro-ops-project-root', 'downloadable': False, 'stationSources': sources}}


def build(root, output, update_report=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    output.relative_to(root/'web/publications/observations')
    base = root/'observations/processed/stations/v1'
    catalog_path = base/'catalog.json'
    frozen = digest(catalog_path)
    catalog = json.loads(catalog_path.read_text())
    updates = update_evidence(root, update_report)
    # Read-only optimistic snapshot: do not take the long-lived updater lock or
    # recover transactions owned by an active update job. Recheck all FNF input
    # entries and source hashes before accepting; unrelated USGS updates are OK.
    fnf_entries = [e for e in catalog['entries'] if e['provider'] == 'cdec'
                   and e['product'] == 'full_natural_flow']
    station_config = root/'config/cdec_fnf_stations.json'
    config_hash = digest(station_config)
    stations = set(json.loads(station_config.read_text())['station_ids'])
    output.mkdir(parents=True, exist_ok=False)
    layers = []
    for resolution in PRODUCTS:
        sensor, duration, _, _, unit = PRODUCTS[resolution]
        entries = [e for e in catalog['entries'] if e['provider'] == 'cdec'
                   and e['product'] == 'full_natural_flow' and e['resolution'] == resolution]
        expected = stations - ({'TNL'} if resolution == 'daily' else set())
        if len(entries) != len(expected) or {e['station_id'] for e in entries} != expected:
            raise ValueError('Incomplete or duplicate configured FNF inventory')
        folder = output/resolution
        folder.mkdir()
        assets, features, sources, cases, gaps = [], [], [], {}, []
        for e in sorted(entries, key=lambda e: e['station_id']):
            source = (base/e['path']).resolve()
            source.relative_to(base)
            if digest(source) != e['sha256']:
                raise ValueError('Source differs from catalog')
            table = read_station(source)
            if (table.metadata['provider'], table.metadata['product'], table.metadata['sensor'],
                    table.metadata['resolution'], table.metadata['units_si']) != (
                        'cdec', 'full_natural_flow', str(sensor), resolution, unit):
                raise ValueError('Unexpected source identity')
            rows = export_rows(table, e['station_id'], resolution)
            if not rows or len(rows) != e['rows']:
                raise ValueError('Record completeness mismatch')
            meta = table.metadata['auxiliary']['station.json']
            lon, lat = meta['longitude'], meta['latitude']
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError('Invalid geometry')
            features.append({'type': 'Feature', 'id': e['station_id'],
                'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': {'station_id': e['station_id'], 'name': meta['station_name'],
                               'basin': meta['river_basin'], 'elevation_raw': meta['elevation_raw'],
                               'metadataUrl': meta['source_url'], 'historical_notes': meta['historical_notes']}})
            fields = table.fields + ['fnf_display_si', 'display_status']
            buffer = io.StringIO(newline='')
            writer = csv.DictWriter(buffer, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            raw = buffer.getvalue().encode('utf-8')
            relative = f'data/{e["station_id"]}/sensor-{sensor}/history.csv.gz'
            path = folder/relative
            path.parent.mkdir(parents=True)
            path.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
            decoded = gzip.decompress(path.read_bytes())
            if decoded != raw or list(csv.DictReader(io.StringIO(decoded.decode(), newline=''))) != rows:
                raise ValueError('Gzip CSV roundtrip mismatch')
            variable = 'fnf_flow' if resolution == 'daily' else 'fnf_volume'
            assets.append({'id': f'{e["station_id"]}-fnf-{resolution}', 'href': relative,
                'seriesId': f'cdec:{e["station_id"]}:fnf:{sensor}:{duration}',
                'featureId': e['station_id'], 'sensor': str(sensor), 'resolution': resolution,
                'variables': [variable], 'mediaType': 'text/csv', 'fileCompression': 'gzip', 'layout': 'wide',
                'featureIdLocation': 'column:station_id', 'sha256': digest(path), 'bytes': path.stat().st_size,
                'partition': {'kind': 'full-station-history'},
                'coverage': {'rows': len(rows), 'firstTimestamp': rows[0]['timestamp_pst'],
                             'lastTimestamp': rows[-1]['timestamp_pst'],
                             'displayStatusCounts': dict(Counter(r['display_status'] for r in rows)),
                             'providerFlagCounts': dict(Counter(r['data_flag'] for r in rows)),
                             'samplingPolicy': 'Absent periods are missing records, never inferred zeros; no interpolation'},
                'columns': {f: {'type': 'number-or-empty' if f in ('value', 'value_si', 'fnf_display_si') else 'string'} for f in fields}})
            for row in rows:
                key = 'zero' if row['value_si'] and float(row['value_si']) == 0 else row['display_status']
                for tag in (key, 'flag:'+row['data_flag']):
                    cases.setdefault(tag, {'asset': relative, 'record': row, 'expectedPlot': {
                        'plotCoordinate': row['timestamp_pst'],
                        'valueSI': float(row['value_si']) if row['value_si'] else None,
                        'valueNative': float(row['value']) if row['value'] else None,
                        'excluded': row['display_status'] == 'missing'}})
            gaps.extend(gap_references(rows, resolution, relative))
            if digest(source) != e['sha256']:
                raise ValueError('Source changed during export')
            update = updates.get(e['path'])
            if update_report is not None and (update is None or update['entry']['sha256'] != e['sha256']):
                raise ValueError('No matching update evidence for current FNF snapshot')
            sources.append({'path': str(source.relative_to(root)), 'sha256': e['sha256'],
                            'downloadable': False,
                            'bootstrapReceipt': table.metadata.get('archive_receipt', {}),
                            'bootstrapReceiptScope': 'Historical initial-ingestion record; counts and dates do not describe the current CSV',
                            'publishedSnapshot': {'rows': len(rows), 'firstPeriod': rows[0]['period_label'],
                                'lastPeriod': rows[-1]['period_label'],
                                'numericRows': sum(bool(r['value_si']) for r in rows)},
                            'subsequentUpdate': update,
                            'updateLineageScope': 'Latest verified refresh yielding this exact source checksum; not a complete per-record correction history' if update else 'No external incremental receipt supplied; source content hash only'})
        doc = manifest(resolution, output.name, assets, sources)
        atomic_json(folder/'collection.json', doc)
        atomic_json(folder/'features.geojson', {'type': 'FeatureCollection', 'features': features})
        atomic_json(folder/'reference-records.json', {'realRecords': cases, 'absentPeriodCases': gaps})
        (folder/'README.txt').write_text(
            f'{doc["title"]}\nRelease: {output.name}\nEntry point: collection.json\n'
            'Decompress .csv.gz before parsing; checksums and sizes describe compressed bytes.\n'
            'Plot fnf_display_si against timestamp_pst, preserving reporting dates/months.\n'
            'Values are already SI. Negative estimates and provider flags are retained; blanks remain missing.\n'
            'Daily flow rates and monthly volumes are different quantities; do not combine on one unit axis.\n'
            'No interpolation, daily-to-monthly reduction, or UTC-day reinterpretation is applied.\n'
            'Detect nonconsecutive reporting dates/months and insert presentation gaps; connectgaps=false alone is insufficient.\n'
            'Provider observation timestamp has unverified meaning; never substitute it for the reporting date.\n'
            'Bootstrap receipts are historical; publishedSnapshot describes this release and subsequentUpdate documents the matched refresh.\n'
            'Naturalized estimates are not measured regulated discharge; review station historical notes.\n')
        accepted = {'status': 'passed', 'stations': len(features), 'records': sum(a['coverage']['rows'] for a in assets),
                    'assets': len(assets), 'files': inventory(folder),
                    'scope': 'Local source-checksum, record completeness and lossless CSV/gzip readback; browser/HTTPS review pending'}
        atomic_json(folder/'acceptance.json', accepted)
        layers.append({'id': doc['id'], 'title': doc['title'], 'href': f'{resolution}/collection.json',
                       'revision': doc['revision'], 'resolution': resolution,
                       **{k: accepted[k] for k in ('stations', 'records', 'assets')}})
    current = json.loads(catalog_path.read_text())
    current_fnf = [e for e in current['entries'] if e['provider'] == 'cdec'
                   and e['product'] == 'full_natural_flow']
    if (sorted(current_fnf, key=lambda e: e['path']) != sorted(fnf_entries, key=lambda e: e['path'])
            or digest(station_config) != config_hash
            or any(digest(base/e['path']) != e['sha256'] for e in fnf_entries)):
        raise ValueError('FNF sources or configuration changed; build a new snapshot')
    for update in updates.values():
        if (digest(root/update['reportPath']) != update['reportSha256']
                or digest(root/update['receiptPath']) != update['receiptSha256']):
            raise ValueError('Update evidence changed during export')
    index = collection_index(output.name, layers, False)
    index['sharedStationPolicy'] = 'Separate provider products/series; daily rates and monthly volumes are distinct quantities, not interchangeable measurements'
    atomic_json(output/'catalog.json', index)
    (output/'README.txt').write_text(
        'CDEC FNF full-history pilot\nEntry point: catalog.json\n'
        'Daily: sensor 8, flow rate (m3 s-1); monthly: sensor 65, volume (m3).\n'
        'TNL has monthly only. Sensor 290 is excluded. Each layer has one gzipped CSV per station.\n'
        'Upload assets before metadata. Local pilot only; no transfer or scheduled publication enabled.\n')
    report = {'status': 'passed', 'layers': layers, 'source_catalog_sha256': frozen,
              'source_catalog_path': str(catalog_path.relative_to(root)),
              'publication_catalog_sha256': digest(output/'catalog.json'),
              'station_config_sha256': config_hash, 'files': inventory(output),
              'scope': 'Local export fidelity only; scientific/browser/HTTPS review separate'}
    verify_catalog_hash(output, report)
    atomic_json(output/'acceptance.json', report)
    return report
